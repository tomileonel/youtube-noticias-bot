import os
import requests
import xml.etree.ElementTree as ET
import psycopg2
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "").strip().rstrip('.')
CHANNEL_ID = os.getenv("CHANNEL_ID")
SUPADATA_KEYS = [k.strip() for k in os.getenv("SUPADATA_KEYS", "").split(',') if k.strip()]
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(',') if k.strip()]

def video_ya_existe(video_id):
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM noticias WHERE video_id = %s", (video_id,))
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists
    except:
        return False

def obtener_transcripcion(video_id):
    for key in SUPADATA_KEYS:
        print(f"Probando Supadata: {key[:5]}...")
        url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}&text=true"
        try:
            res = requests.get(url, headers={"x-api-key": key}, timeout=30)
            if res.status_code == 200:
                return res.json().get('content')
        except:
            continue
    return None

def generar_noticia(texto, titulo):
    for key in GEMINI_KEYS:
        print(f"Probando Gemini: {key[:5]}...")
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel('gemini-2.5-flash-lite')
            prompt = f"Escribe una noticia profesional EXTENSA en HTML. Título: {titulo}. Contenido: {texto}. Responde solo HTML."
            response = model.generate_content(prompt)
            return response.text.replace('```html', '').replace('```', '').strip()
        except:
            continue
    return None

def run():
    # 1. RSS: Tomamos SOLO el último video
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    response = requests.get(rss_url)
    tree = ET.fromstring(response.content)
    
    # .find() devuelve solo el primer 'entry' encontrado (el más reciente)
    entry = tree.find('{http://www.w3.org/2005/Atom}entry')
    if entry is None:
        print("No se encontraron videos.")
        return

    v_id = entry.find('{http://www.youtube.com/xml/schemas/2015}videoId').text
    v_titulo = entry.find('{http://www.w3.org/2005/Atom}title').text

    # 2. Verificamos si ya está en DB
    if video_ya_existe(v_id):
        print(f"✅ El video '{v_titulo}' ya existe. No se gastarán tokens.")
        return

    # 3. Procesamiento
    print(f"🚀 Procesando video: {v_titulo}")
    texto = obtener_transcripcion(v_id)
    if texto:
        html = generar_noticia(texto, v_titulo)
        if html:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("INSERT INTO noticias (video_id, title, content, published_at) VALUES (%s, %s, %s, NOW())", (v_id, v_titulo, html))
            conn.commit()
            conn.close()
            print("✨ Éxito: Noticia guardada.")

if __name__ == "__main__":
    run()