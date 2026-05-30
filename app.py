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

# Image & OCR processing libraries
from pdf2image import convert_from_path
import numpy as np

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
API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    raise RuntimeError("CRITICAL ERROR: GOOGLE_API_KEY environment variable is not set!")

genai.configure(api_key=API_KEY)


def extract_text_with_ocr(file_path):
    """
    Fallback OCR engine using EasyOCR to transcribe image-based or scanned PDFs
    without needing system-level Linux apt updates.
    """
    print("[OCR Pipeline] Image-based document layout detected. Initializing EasyOCR framework...")
    try:
        import easyocr
        # Initialize the transcription reader target language to English
        reader = easyocr.Reader(['en'])
        
        # Convert the first 5 pages of the PDF into flat images (safeguards cloud memory spikes)
        pages = convert_from_path(file_path, first_page=1, last_page=5)
        ocr_text = ""
        
        for i, page in enumerate(pages):
            print(f"[OCR Pipeline] Processing matrix scanner arrays on Page {i+1}...")
            # Convert PIL Image object to a structured numpy array for easyocr processing
            page_array = np.array(page)
            results = reader.readtext(page_array, detail=0)
            page_text = " ".join(results)
            ocr_text += f"\n--- Scanned Page {i+1} ---\n" + page_text
            
        return ocr_text
    except Exception as ocr_err:
        print(f"[OCR Pipeline Critical Failure] Fallback skipped: {str(ocr_err)}")
        return ""


def local_extract_text_from_pdf(file_path):
    """Two-tier extractor: Tries native text maps first, falls back to OCR if empty."""
    reader = PdfReader(file_path)
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    # Trigger intelligent image transcription fallback layer if text density is non-existent
    if len(full_text.strip()) < 50:
        full_text = extract_text_with_ocr(file_path)
        
    return full_text


async def call_gemini_with_dynamic_configs(prompt, creativity=0.7, max_retries=3):
    """Interacts with Gemini AI Studio passing advanced dashboard settings parameter configurations."""
    safe_temperature = float(creativity)
    
    generation_config = {
        "temperature": safe_temperature,
        "response_mime_type": "application/json"
    }
    
    # Utilizing gemini-1.5-flash for strong structural JSON response extraction controls
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config
    )
    
    last_exception = None
    delay = 2  

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Backend Engine] Sending request to Google AI Studio (Attempt {attempt}/{max_retries}) with Temp: {safe_temperature}...")
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
async def generate_presentation(
    file: UploadFile = File(...), 
    theme_color: str = Form("#0284c7"),
    slide_length: str = Form("medium"),
    creativity: str = Form("0.7"),
    audience: str = Form("General Public")
):
    temp_pdf_path = os.path.join(OUTPUT_DIR, f"temp_{os.urandom(4).hex()}_{file.filename}")

    try:
        with open(temp_pdf_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        print("[Backend Pipeline] Extracting text layers using hybrid extraction strategy...")
        pdf_text = await asyncio.to_thread(local_extract_text_from_pdf, temp_pdf_path)
        
        if not pdf_text.strip():
            raise HTTPException(status_code=400, detail="No readable digital text or scanned structures found inside the PDF.")

        # Map frontend size configurations into explicit instruction strings
        length_mapping = {
            "short": "exactly between 3 to 5 slides total",
            "medium": "exactly between 6 to 10 slides total",
            "long": "exactly between 11 to 15 slides total"
        }
        target_length = length_mapping.get(slide_length, "exactly between 6 to 10 slides total")

        prompt = f"""
        You are a presentation engine acting exactly like NotebookLM. Analyze the following text context and break it down into a highly educational, comprehensive slide-by-slide storyboard. 
        
        CRITICAL PRESENTATION BLUEPRINT RULES:
        1. Target Presentation Size: You must construct the presentation layout to have {target_length}.
        2. Target Audience Profile: Adapt all narrative tone, context depths, and explanations specifically to engage a: '{audience}'.
        
        Output your response ONLY as a valid JSON array matching this exact format:
        [
          {{
            "title": "Slide Title Here",
            "points": ["Insight point 1", "Detailed point 2"]
          }}
        ]
        Context:
        {pdf_text[:120000]}
        """
        
        structured_slides = await call_gemini_with_dynamic_configs(prompt, creativity=creativity, max_retries=3)
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
    """Reads index.html with a fallback encoding handler to prevent crashes on Linux environments."""
    index_path = "index.html"
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                return f.read()
        except Exception as read_err:
            raise HTTPException(status_code=500, detail=f"File read failure: {str(read_err)}")
            
    raise HTTPException(status_code=404, detail="index.html file was not found in the root repository path.")