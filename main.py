from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import json
import asyncio
import uuid
import logging
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
        </style>
    </head>
    <body>
        <h1>Electronic Device Recommender</h1>
        <form id="search-form">
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
            
            <div class="form-group">
                <label>强制重新搜索（绕过缓存）</label>
                <input type="checkbox" name="refresh" value="true" />
            </div>

            </div>

            <button type="submit">Search</button>
        </form>

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

                // 显示搜索条件（可选）
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

                        // ---------- 1. 计算匹配关键词 ----------
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

                        // ---------- 2. 生成各部件 HTML ----------
                        // 相似度
                        const simHtml = `<div style="font-size:0.95em; color:#28a745; font-weight:bold; margin:2px 0;">
                            Similarity: ${(prod.similarity * 100).toFixed(2)}%
                        </div>`;

                        // 关键词（蓝色）
                        const keywordHtml = matchedKeywords.length > 0 ? 
                            `<div style="color:#007bff; font-size:0.9em; margin:2px 0;">🔑 Keywords: ${matchedKeywords.join(' ')}</div>` : 
                            `<div style="color:#999; font-size:0.85em; margin:2px 0;">🔑 Keywords: (none matched)</div>`;

                        // AI证据句（清晰、干净）
                        const evidenceHtml = prod.evidence_sentence ? 
                            `<div style="color:#555; font-size:0.95em; margin:4px 0;">💡 ${prod.evidence_sentence}</div>` : 
                            `<div style="color:#999; font-size:0.9em; margin:4px 0;">💡 No description available.</div>`;
                        
                        const categoryLabels = {
                            'ecommerce': '🛒 E-commerce',
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
                        // ---------- 3. 构建卡片（按新结构） ----------
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

# ---------- Endpoints ----------
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