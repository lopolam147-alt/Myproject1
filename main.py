"""
FastAPI application for Electronic Device Recommender.
Provides web UI, search endpoint, and progress streaming.
"""

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import json
import asyncio
import uuid
from rag_agent import RAGAgent

app = FastAPI(title="Electronic Device Recommender")
agent = RAGAgent()

# Store progress for each request
progress_store = {}

# ---------- Embedded HTML UI ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Device Recommender</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 2em; }
            .form-group { margin-bottom: 1em; }
            label { display: inline-block; width: 120px; }
            input { width: 300px; padding: 5px; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; }
            #progress-container { margin-top: 20px; display: none; }
            #progress-bar { width: 0%; height: 20px; background: #28a745; transition: width 0.3s; }
            #progress-message { margin-left: 10px; }
            #results { margin-top: 20px; }
            .product { border: 1px solid #ddd; padding: 10px; margin: 5px 0; }
            .product .title { font-weight: bold; }
            .product .reason { color: #555; font-style: italic; }
            .product .price { color: #28a745; }
            .form-group {
                display: flex;
                align-items: center;
                margin-bottom: 1.2em;
            }
            .input-group {
                display: flex;
                align-items: center;
                flex-shrink: 0;
            }
            .input-group label {
                width: 130px;
                font-weight: bold;
            }
            .input-group input {
                width: 280px;
                padding: 8px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            .hint-box {
                flex: 1;                       /* 占据剩余宽度 */
                margin-left: 20px;
                padding: 6px 16px;
                background: #f0f8ff;           /* 浅蓝色背景 */
                border: 1px solid #b8d4f0;
                border-radius: 6px;
                color: #0056b3;
                font-size: 0.9em;
                font-weight: 500;
                /* 可选：文字居中 */
                /* text-align: center; */
            }
        </style>
    </head>
    <body>
        <h1>Electronic Device Recommender</h1>
        <div class="form-group">
            <form id="search-form">
                <!-- The system performs one combined search with all user inputs.
                    It does not spawn separate searches for each brand-color pair
                    (e.g., "Samsung blue" vs "Apple red").
                    Multiple brands/colors are mixed together in a single query.
                -->
                <!-- 1. Device Type (Mandatory) -->
                <div class="form-group">
                    <label>Device type <span style="color:red;">*</span></label>
                    <input type="text" name="device_type" placeholder="e.g. laptop..." required />
                </div>

                <!-- 2. Brands (Optional) -->
                <div class="form-group">
                    <label>Brands</label>
                    <input type="text" name="brands" placeholder="e.g. Apple, Samsung, ASUS..." />
                </div>

                <!-- 3. Color (Optional) -->
                <div class="form-group">
                    <label>Color</label>
                    <input type="text" name="color" placeholder="e.g. Sierra blue, Green..." />
                </div>

                <!-- 4. Version (Optional) -->
                <div class="form-group">
                    <label>Version</label>
                    <input type="text" name="version" placeholder="e.g. A54, S26, Z Fold7..." />
                </div>

                <!-- 5. Others (Optional) -->
                <div class="form-group">
                    <label>Others</label>
                    <input type="text" name="others" placeholder="e.g. waterproof, 4K, OLED..." />
                </div>

                <!-- 6. Refresh (Optional) -->
                <div class="form-group">
                    <label>Force refresh (bypass cache)</label>
                    <input type="checkbox" name="refresh" value="true" />
                </div>
                
                <button type="submit">Search</button>
            </form>
            <div class="hint-box">
            <strong>Rules:</strong><br>
            1. The system searches all your keywords; Keywords section shows which terms were found on the page.<br>
            2. For best results, enter only one brand and one color per field. The selection of multiple color and brand are not paired — they are mixed in a single search (just one pair for 5 single input).<br>
            3. Classification prioritizes e-commerce pages (buying links, prices).<br>
            4. For some products is out of stock in one link, it is more prefer you to use another one website.<br>
            5. Please enter suitable electronic device.
            </div>
        </div>

        <div id="progress-container">
            <div style="display: flex; align-items: center;">
                <div style="flex:1; background:#e9ecef; height:20px; border-radius:5px; overflow:hidden;">
                    <div id="progress-bar" style="width:0%; height:100%; background:#28a745; transition: width 0.3s;"></div>
                </div>
                <span id="progress-message" style="margin-left:10px;">0%</span>
            </div>
        </div>

        <div id="results"></div>

        <script>
            const form = document.getElementById('search-form');
            const progressContainer = document.getElementById('progress-container');
            const progressBar = document.getElementById('progress-bar');
            const progressMsg = document.getElementById('progress-message');
            const resultsDiv = document.getElementById('results');

            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const formData = new FormData(form);
                resultsDiv.innerHTML = '';
                progressContainer.style.display = 'block';
                progressBar.style.width = '0%';
                progressMsg.textContent = '0%';

                try {
                    const response = await fetch('/recommend', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await response.json();
                    const progressId = data.progress_id;

                    const eventSource = new EventSource(`/progress/${progressId}`);
                    eventSource.onmessage = (event) => {
                        const update = JSON.parse(event.data);
                        const progress = update.progress || 0;
                        const message = update.message || '';
                        progressBar.style.width = progress + '%';
                        progressMsg.textContent = `${progress}% - ${message}`;
                    };
                    eventSource.addEventListener('result', (event) => {
                        const result = JSON.parse(event.data);
                        eventSource.close();
                        displayResults(result);
                        progressMsg.textContent = 'Done!';
                    });
                    eventSource.onerror = () => {
                        eventSource.close();
                        pollResult(progressId);
                    };
                } catch (err) {
                    console.error(err);
                    progressMsg.textContent = 'Error occurred.';
                }
            });

            async function pollResult(progressId) {
                let attempts = 0;
                const interval = setInterval(async () => {
                    attempts++;
                    const resp = await fetch(`/result/${progressId}`);
                    if (resp.status === 200) {
                        const result = await resp.json();
                        clearInterval(interval);
                        displayResults(result);
                    } else if (attempts > 60) {
                        clearInterval(interval);
                        progressMsg.textContent = 'Timeout - search took too long.';
                    }
                }, 1000);
            }

            function displayResults(result) {
                const recs = result.recommendations || [];
                const message = result.message || '';
                const userInput = result.user_input || {};
                let html = `<p><strong>${message}</strong></p>`;

                // Show search criteria
                let criteria = [];
                if (userInput.device_type) criteria.push(`Device: ${userInput.device_type}`);
                if (userInput.brands) criteria.push(`Brand: ${userInput.brands}`);
                if (userInput.version) criteria.push(`Version: ${userInput.version}`);
                if (userInput.color) criteria.push(`Color: ${userInput.color}`);
                if (userInput.others) criteria.push(`Others: ${userInput.others}`);
                if (criteria.length > 0) {
                    html += `<div style="margin-bottom:12px; color:#444; font-size:0.95em; background:#f5f5f5; padding:8px 12px; border-radius:4px;">
                        <strong>🔍 Search Criteria:</strong> ${criteria.join(' | ')}
                    </div>`;
                }

                if (recs.length === 0) {
                    html += '<p>No recommendations found.</p>';
                } else {
                    recs.forEach((prod, idx) => {
                        const isBlocked = prod.blocked === true;
                        const blockReason = prod.block_reason || '';
                        const cardStyle = isBlocked ? 'opacity: 0.7; background: #f9f9f9;' : '';
                        const linkStyle = isBlocked ? 'color: #999; cursor: not-allowed;' : '';

                        // ---------- 1. Calculate matched keywords ----------
                        let matchedKeywords = [];
                        const userFields = {
                            brands: userInput.brands,
                            version: userInput.version,
                            device_type: userInput.device_type,
                            color: userInput.color,
                            others: userInput.others
                        };
                        const allText = (prod.title || '') + ' ' + (prod.evidence_sentence || '');
                        const cleanText = allText.replace(/\\s+/, '').toLowerCase();

                        for (const [field, value] of Object.entries(userFields)) {
                            if (value && value.trim()) {
                                const words = value.split(/\\s+/).filter(w => w.length > 1);
                                for (const word of words) {
                                    const cleanWord = word.replace(/\\s+/, '').toLowerCase();
                                    if (cleanText.includes(cleanWord)) {
                                        matchedKeywords.push(word);
                                    }
                                }
                            }
                        }
                        matchedKeywords = [...new Set(matchedKeywords)].slice(0, 8);

                        // ---------- 2. Generate HTML components ----------
                        const simHtml = `<div style="font-size:0.95em; color:#28a745; font-weight:bold; margin:2px 0;">
                            Similarity: ${(prod.similarity * 100).toFixed(2)}%
                        </div>`;

                        const keywordHtml = matchedKeywords.length > 0 ? 
                            `<div style="color:#007bff; font-size:0.9em; margin:2px 0;">🔑 Keywords: ${matchedKeywords.join(' ')}</div>` : 
                            `<div style="color:#999; font-size:0.85em; margin:2px 0;">🔑 Keywords: (none matched)</div>`;

                        const evidenceHtml = prod.evidence_sentence ? 
                            `<div style="color:#555; font-size:0.95em; margin:4px 0;">💡 ${prod.evidence_sentence}</div>` : 
                            `<div style="color:#999; font-size:0.9em; margin:4px 0;">💡 No description available.</div>`;
                        
                        const categoryLabels = {
                            'ecommerce': '🛒 E-commerce / Official selling page',
                            'homepage': '🏠 E-commerce Homepage',
                            'landing_page': '📋 Listing Page', 
                            'official_spec': '📋 Official Specs',
                            'resale': '🔄 Resale',
                            'review': '📝 Review',
                            'forum': '💬 Forum',
                            'news': '📰 News',
                            'unknown': '🔗 Web'
                        };
                        const categoryHtml = prod.category ? 
                            `<div style="color:#888; font-size:0.8em; margin:2px 0;">${categoryLabels[prod.category] || prod.category}</div>` : '';

                        // ---------- 3. Build product card ----------
                        html += `
                            <div class="product" style="${cardStyle}">
                                <div class="title" style="font-size:1.1em; font-weight:bold;">${idx+1}. ${prod.title}</div>
                                ${simHtml}
                                ${keywordHtml}
                                ${evidenceHtml}
                                ${categoryHtml}
                                <div style="margin-top:6px;">
                                    <a href="${prod.url}" target="_blank" style="${linkStyle}">View product</a>
                                    ${isBlocked ? `<span style="color:red; margin-left:10px; font-weight:bold;">🚫 ${blockReason}</span>` : ''}
                                </div>
                            </div>
                            <hr style="border: 0.5px solid #eee; margin: 15px 0;">
                        `;
                    });
                }
                resultsDiv.innerHTML = html;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ---------- Background agent runner (non‑blocking) ----------
async def run_agent(progress_id: str, user_input: dict):
    def update(progress, message):
        progress_store[progress_id] = {"progress": progress, "message": message}

    try:
        result = await asyncio.to_thread(agent.process_request, user_input, update)
        progress_store[progress_id]["result"] = result
    except Exception as e:
        import logging
        logging.error(f"run_agent crashed: {e}", exc_info=True)
        progress_store[progress_id]["result"] = {
            "recommendations": [],
            "total_found": 0,
            "message": f"Agent error: {str(e)}",
            "source": "error"
        }
    finally:
        progress_store[progress_id]["done"] = True

# ---------- API Endpoints ----------
@app.post("/recommend")
async def recommend(
    device_type: str = Form(""),
    brands: str = Form(""),
    color: str = Form(""),
    version: str = Form(""),
    others: str = Form(""),
    refresh: bool = Form(False)
):
    user_input = {
        "device_type": device_type,
        "brands": brands,
        "color": color,
        "version": version,
        "others": others,
        "refresh": refresh,
    }
    progress_id = str(uuid.uuid4())
    progress_store[progress_id] = {"progress": 0, "message": "Starting..."}

    asyncio.create_task(run_agent(progress_id, user_input))

    return JSONResponse({"progress_id": progress_id})


@app.get("/progress/{progress_id}")
async def progress_stream(progress_id: str):
    async def event_generator():
        while True:
            data = progress_store.get(progress_id)
            if data:
                yield f"data: {json.dumps({'progress': data.get('progress', 0), 'message': data.get('message', '')})}\n\n"
                if data.get("done"):
                    result = data.get("result")
                    if result:
                        yield f"event: result\ndata: {json.dumps(result)}\n\n"
                    break
            await asyncio.sleep(0.5)  # poll every 500ms
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/result/{progress_id}")
async def get_result(progress_id: str):
    data = progress_store.get(progress_id)
    if data and data.get("done"):
        return JSONResponse(data.get("result", {}))
    return JSONResponse({"status": "pending"}, status_code=202)