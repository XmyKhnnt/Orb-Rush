# 🔮 Orb Rush

> A realtime multiplayer orb collection game — built for **fun and learning only**.

Players race to grab orbs on a shared 2000×2000 canvas before time runs out. No accounts, no data stored, no stakes.

---

## 🎮 How It Works

| Feature | Detail |
|---|---|
| Players | Up to 25 per session |
| Orb types | Common · Rare · Epic |
| Round length | 10 minutes |
| Names | Auto-generated (e.g. *Neon Vortex*, *Ghost Titan*) |

---

## 🛠 Stack

- **Frontend** — vanilla HTML / CSS / Canvas API (`index.html`)
- **Backend** — Python · FastAPI · WebSockets (`server.py`)

---

## 🚀 Run Locally

```bash
pip install fastapi uvicorn websockets
uvicorn server:app --reload
```

Open `http://localhost:8000`.

---

## ☁️ Deploy on Render

1. Push repo to GitHub
2. New **Web Service** → connect repo
3. **Build Command:** `pip install fastapi uvicorn`
4. **Start Command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`

---

## ⚠️ Disclaimer

**This project is provided strictly for educational and personal entertainment purposes.**

- No warranty of any kind — use at your own risk
- Not intended for commercial use
- Author(s) accept **no liability** for any loss, damage, claim, or legal action arising from use, misuse, or inability to use this software
- No user data is collected, stored, or transmitted beyond what is required for the game session (in-memory only, discarded on disconnect)
- This software is provided **"as is"** without any express or implied warranty, including but not limited to fitness for a particular purpose or non-infringement

---

## 📄 License

[MIT License](https://opensource.org/licenses/MIT) — free to use, modify, and distribute with no restrictions.

> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
