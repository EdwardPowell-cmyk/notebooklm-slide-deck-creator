import os
import json
import time
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
from dotenv import load_dotenv
from ppt_builder_v2 import create_rich_deck

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure output directories exist dynamically for local and cloud environments
OUTPUT_DIR = os.path.abspath("./outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# --- FIXED LOGIC GAP: RESTORED API KEY CORNERSTONE SETTING ---
# Looks for the secure cloud environment variable first, then uses your hardcoded backup key
API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyCc45aIKZRV6I2pUl36B1pQzcxPEv5wg1Y")
genai.configure(api_key=API_KEY)

async def call_gemini_with_retry(gemini_file, prompt, max_retries=3):
    """
    Executes the core AI extraction layer. If a transient failure occurs,
    it automatically attempts execution up to 3 times with progressive delays.
    """
    model = genai.GenerativeModel("gemini-3.1-flash-lite")
    last_exception = None
    delay = 2  # Starting delay in seconds

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[Backend Pipeline] Contacting Google AI Studio (Attempt {attempt}/{max_retries})...")
            # Run the synchronous Gemini library inside a thread pool to avoid blocking FastAPI
            response = await asyncio.to_thread(model.generate_content, [gemini_file, prompt])
            
            # Basic structural verification of data payload
            clean_text = response.text.strip().lstrip("```json").rstrip("```").strip()
            structured_slides = json.loads(clean_text)
            
            print(f"[Backend Pipeline] Success on attempt {attempt}!")
            return structured_slides

        except (json.JSONDecodeError, Exception) as e:
            last_exception = e
            print(f"[Backend Pipeline] Attempt {attempt} failed due to: {str(e)}")
            if attempt < max_retries:
                print(f"[Backend Pipeline] Retrying engine execution in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff scaling
            else:
                print("[Backend Pipeline] All 3 validation attempts exhausted.")
    
    raise last_exception

@app.post("/api/generate-presentation")
async def generate_presentation(file: UploadFile = File(...), theme_color: str = Form("#0284c7")):
    temp_pdf_path = os.path.join(OUTPUT_DIR, f"temp_{os.urandom(4).hex()}_{file.filename}")
    gemini_file = None

    try:
        # Step 1: Write incoming stream chunks to isolated local memory
        with open(temp_pdf_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        # Step 2: Upload payload chunk straight to Gemini Cloud Layer
        try:
            gemini_file = await asyncio.to_thread(genai.upload_file, path=temp_pdf_path)
        except Exception as upload_err:
            raise HTTPException(status_code=502, detail="Google AI Studio connectivity lost during document chunk upload.")

        # Step 3: Run the structured AI analysis with built-in 3x retry automation logic
        prompt = """
        You are a presentation engine acting exactly like NotebookLM. Analyze the attached document and break it down into a highly educational, comprehensive slide-by-slide storyboard. Create as many slides as necessary to exhaustively cover the key concepts. Do not summarize abstractly; capture specific insights.

        Output your response ONLY as a valid JSON array matching this exact format:
        [
          {
            "title": "Slide Title Here",
            "points": ["Insight point 1", "Detailed point 2", "Data point 3"]
          }
        ]
        """
        
        try:
            structured_slides = await call_gemini_with_retry(gemini_file, prompt, max_retries=3)
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="AI returned invalid formatting layout. Retries exhausted.")
        except Exception as ai_err:
            raise HTTPException(status_code=502, detail=f"Google AI Studio engine failed to process document. Details: {str(ai_err)}")

        # Step 4: Map slides to PowerPoint layout graphics compiler
        task_id = "presentation_" + os.urandom(4).hex()
        output_pptx_filename = f"{task_id}_rich_presentation.pptx"
        output_pptx_path = os.path.join(OUTPUT_DIR, output_pptx_filename)
        
        # Execute v2 drawing script
        await asyncio.to_thread(create_rich_deck, task_id, structured_slides, theme_color_hex=theme_color)

        return {
            "status": "success",
            "download_path": f"outputs/{output_pptx_filename}",
            "total_slides_generated": len(structured_slides)
        }

    except HTTPException as http_ex:
        raise http_ex
    except Exception as general_ex:
        raise HTTPException(status_code=500, detail=f"Internal App Error: {str(general_ex)}")
        
    finally:
        # Ensure cleanup cycles run completely to preserve container space
        if gemini_file:
            try:
                await asyncio.to_thread(genai.delete_file, gemini_file.name)
            except:
                pass
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

# --- NEW DEPLOYMENT BINDING: SERVE THE FRONTEND DIRECTLY FROM THE INTERNET ---
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """
    Listens to the primary root web domain of your application and delivers 
    the visual 'index.html' control dashboard directly to the client's browser.
    """
    index_path = os.path.abspath("./index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    raise HTTPException(status_code=404, detail="Dashboard UI source file (index.html) not found in project directory.")