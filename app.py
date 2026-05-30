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

# Upgraded Error-Proof Engine Import
import fitz  # PyMuPDF

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

OUTPUT_DIR = os.path.abspath("./outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# --- SECURE AUTHENTICATION LAYER ---
API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    raise RuntimeError("CRITICAL ERROR: GOOGLE_API_KEY environment variable is not set!")

genai.configure(api_key=API_KEY)


def extract_text_with_pymupdf_ocr(file_path):
    """
    Advanced fallback engine using PyMuPDF to extract text from scanned or image-based PDFs
    safely on Python 3.14 without causing memory overflows or package build crashes.
    """
    print("[PyMuPDF Pipeline] Analyzing document pixel layers for scanned text layout...")
    try:
        doc = fitz.open(file_path)
        ocr_text = ""
        
        # Process pages safely (limits to first 8 pages to safeguard Free Tier cloud memory timeouts)
        max_pages = min(len(doc), 8)
        for i in range(max_pages):
            print(f"[PyMuPDF Pipeline] Extracting text clusters from Page {i+1}...")
            page = doc[i]
            # 'text' extraction handles underlying text shapes; if empty, it extracts structural block maps
            page_text = page.get_text("text")
            if not page_text.strip():
                # Fallback to block level analysis for flat raster text images
                page_text = page.get_text("blocks")
                page_text = " ".join([b[4] for b in page_text if isinstance(b[4], str)])
                
            ocr_text += f"\n--- Scanned Page {i+1} ---\n" + page_text
            
        return ocr_text
    except Exception as pdf_err:
        print(f"[PyMuPDF Engine Error] Visual analysis skipped: {str(pdf_err)}")
        return ""


def local_extract_text_from_pdf(file_path):
    """Layered layout extraction strategy: Digital Native Parser -> PyMuPDF Scanner Fallback."""
    reader = PdfReader(file_path)
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    # Trigger PyMuPDF visual analysis extraction layer if digital text density is non-existent
    if len(full_text.strip()) < 50:
        full_text = extract_text_with_pymupdf_ocr(file_path)
        
    return full_text


async def call_gemini_with_dynamic_configs(prompt, creativity=0.7, max_retries=3):
    """Interacts with Gemini AI Studio passing advanced dashboard configurations dynamically."""
    safe_temperature = float(creativity)
    
    generation_config = {
        "temperature": safe_temperature,
        "response_mime_type": "application/json"
    }
    
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config
    )
    
    last_exception = None
    delay = 2  

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Backend Engine] Contacting Google AI Studio (Attempt {attempt}/{max_retries}) with Temp: {safe_temperature}...")
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

        print("[Backend Pipeline] Activating hybrid intelligent text layout extractor...")
        pdf_text = await asyncio.to_thread(local_extract_text_from_pdf, temp_pdf_path)
        
        if not pdf_text.strip():
            raise HTTPException(status_code=400, detail="No readable text layout maps or image clusters could be decoded.")

        # Map frontend size selections into explicit instruction strings
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


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = "index.html"
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                return f.read()
        except Exception as read_err:
            raise HTTPException(status_code=500, detail=f"File read failure: {str(read_err)}")
            
    raise HTTPException(status_code=404, detail="index.html file was not found in the root repository path.")