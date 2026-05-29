import os
import json
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
from dotenv import load_dotenv
from pypdf import PdfReader
from ppt_builder_v2 import create_rich_deck

# Load local .env file variables if testing on your laptop
load_dotenv()

app = FastAPI()

# Enable global CORS permissions
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Render creates directories in the root folder automatically
OUTPUT_DIR = os.path.abspath("./outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# --- SECURE AUTHENTICATION LAYER ---
# Fetches the key securely from the environment system
API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    # This prevents the app from hanging for 30 seconds if the key is missing
    raise RuntimeError("CRITICAL ERROR: GOOGLE_API_KEY environment variable is not set!")

genai.configure(api_key=API_KEY)


def local_extract_text_from_pdf(file_path):
    reader = PdfReader(file_path)
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
    return full_text


async def call_gemini_with_retry(prompt, max_retries=3):
    model = genai.GenerativeModel("gemini-3.1-flash-lite")
    last_exception = None
    delay = 2  

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Backend Pipeline] Contacting Google AI Studio (Attempt {attempt}/{max_retries})...")
            response = await asyncio.to_thread(model.generate_content, prompt)
            clean_text = response.text.strip().lstrip("```json").rstrip("```").strip()
            structured_slides = json.loads(clean_text)
            return structured_slides
        except (json.JSONDecodeError, Exception) as e:
            last_exception = e
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2  
    raise last_exception


@app.post("/api/generate-presentation")
async def generate_presentation(file: UploadFile = File(...), theme_color: str = Form("#0284c7")):
    temp_pdf_path = os.path.join(OUTPUT_DIR, f"temp_{os.urandom(4).hex()}_{file.filename}")

    try:
        with open(temp_pdf_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        print("[Backend Pipeline] Extracting text locally...")
        pdf_text = await asyncio.to_thread(local_extract_text_from_pdf, temp_pdf_path)
        
        if not pdf_text.strip():
            raise HTTPException(status_code=400, detail="No readable text found inside PDF.")

        prompt = f"""
        You are a presentation engine acting exactly like NotebookLM. Analyze the following text context and break it down into a highly educational, comprehensive slide-by-slide storyboard. Output your response ONLY as a valid JSON array matching this exact format:
        [
          {{
            "title": "Slide Title Here",
            "points": ["Insight point 1", "Detailed point 2"]
          }}
        ]
        Context:
        {pdf_text[:120000]}
        """
        
        structured_slides = await call_gemini_with_retry(prompt, max_retries=3)
        task_id = "presentation_" + os.urandom(4).hex()
        output_pptx_filename = f"{task_id}_rich_presentation.pptx"
        
        await asyncio.to_thread(create_rich_deck, task_id, structured_slides, theme_color_hex=theme_color)

        return {
            "status": "success",
            "download_path": f"outputs/{output_pptx_filename}",
            "total_slides_generated": len(structured_slides)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


# --- CLEAN STREAMLINED ROOT ROUTER ---
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """
    Reads index.html with a fallback encoding handler ('utf-8-sig' and 'errors=ignore')
    to prevent UnicodeDecodeError crashes on Render's Linux environment.
    """
    index_path = "index.html"
    if os.path.exists(index_path):
        try:
            # 'utf-8-sig' automatically handles hidden Windows BOM markers like 0xff
            with open(index_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                return f.read()
        except Exception as read_err:
            raise HTTPException(status_code=500, detail=f"File read failure: {str(read_err)}")
            
    raise HTTPException(status_code=404, detail="index.html file was not found in the root repository path.")