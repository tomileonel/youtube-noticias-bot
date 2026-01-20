import os
import requests
import xml.etree.ElementTree as ET
import psycopg2
import google.generativeai as genai
from dotenv import load_dotenv

# Cargar variables del archivo .env
load_dotenv()

# --- CONFIGURACIÓN SEGURA ---
# Usamos .get() con un string vacío por defecto para evitar NameError
DB_URL = os.getenv("DATABASE_URL", "").strip().rstrip('.')
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

# Listas de tokens
SUPADATA_KEYS = [k.strip() for k in os.getenv("SUPADATA_KEYS", "").split(',') if k.strip()]
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(',') if k.strip()]

def video_ya_existe(video_id):
    if not DB_URL:
        print("❌ Error: DATABASE_URL no está configurada.")
        return False
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM noticias WHERE video_id = %s", (video_id,))
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists
    except Exception as e:
        print(f"❌ Error DB Check: {e}")
        return False

def obtener_transcripcion(video_id):
    for key in SUPADATA_KEYS:
        print(f"--- Intentando Supadata con token: {key[:6]}... ---")
        url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}"
        headers = {"x-api-key": key}
        try:
            res = requests.get(url, headers=headers, timeout=30)
            if res.status_code == 200:
                data = res.json()
                segmentos = data.get('content', [])
                if isinstance(segmentos, list):
                    texto_final = " ".join([s.get('text', '') for s in segmentos if 'text' in s])
                    if texto_final.strip():
                        print(f"✅ Transcripción procesada ({len(texto_final)} caracteres).")
                        return texto_final
            print(f"⚠️ Supadata Status {res.status_code}")
        except: continue
    return None

def generar_noticia(texto, titulo):
    for key in GEMINI_KEYS:
        print(f"--- Intentando Gemini con token: {key[:6]}... ---")
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = (
                f"Actúa como un periodista profesional. Redacta una noticia EXTENSA en HTML.\n"
                f"TÍTULO: {titulo}\n"
                f"CONTENIDO BASE: {texto}\n\n"
                f"REGLAS: Responde ÚNICAMENTE con el código HTML usando <h2>, <h3>, <p> y <strong>. Sin markdown."
            )
            response = model.generate_content(prompt)
            if response and response.text:
                return response.text.replace('```html', '').replace('```', '').strip()
        except: continue
    return None

def run():
    if not CHANNEL_ID:
        print("❌ Error: CHANNEL_ID no configurado.")
        return

    # Limpieza de URL para evitar InvalidSchema
    canal_limpio = CHANNEL_ID.replace('[', '').replace(']', '').strip()
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_limpio}"
    
    print(f"🚀 Conectando a: {rss_url}")
    
    try:
        res_rss = requests.get(rss_url, timeout=15)
        res_rss.raise_for_status()
        tree = ET.fromstring(res_rss.content)
        entry = tree.find('{http://www.w3.org/2005/Atom}entry')
        
        if entry is None:
            print("❌ No se encontraron videos.")
            return

        v_id = entry.find('{http://www.youtube.com/xml/schemas/2015}videoId').text
        v_titulo = entry.find('{http://www.w3.org/2005/Atom}title').text
    except Exception as e:
        print(f"❌ Error al obtener RSS: {e}")
        return

    if video_ya_existe(v_id):
        print(f"🛑 El video '{v_titulo}' ya fue procesado.")
        return

    print(f"🎬 Procesando: {v_titulo}")
    texto = obtener_transcripcion(v_id)
    if not texto: return

    html = generar_noticia(texto, v_titulo)
    if not html: return

    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO noticias (video_id, title, content, published_at) VALUES (%s, %s, %s, NOW())",
            (v_id, v_titulo, html)
        )
        conn.commit()
        cur.close()
        conn.close()
        print("✨ TODO OK: Noticia guardada.")
    except Exception as e:
        print(f"❌ Error al insertar en Postgres: {e}")

if __name__ == "__main__":
    run()