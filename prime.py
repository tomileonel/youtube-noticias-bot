import os
import google.generativeai as genai
from dotenv import load_dotenv
load_dotenv()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not GEMINI_API_KEY:
    print("❌ Error: No hay API Key de Gemini configurada.")
    exit()

genai.configure(api_key=GEMINI_API_KEY)

def escanear_modelos():
    print("\n🔍 --- ESCANEANDO MODELOS DISPONIBLES ---")
    print(f"Usando librería versión: {genai.__version__}")
    
    try:
        # Listamos todos los modelos disponibles para tu cuenta
        hay_modelos = False
        for m in genai.list_models():
            # Filtramos solo los que sirven para generar texto (generateContent)
            if 'generateContent' in m.supported_generation_methods:
                print(f"✅ MODELO ACTIVO: {m.name}")
                print(f"   (Descripción: {m.displayName})")
                hay_modelos = True
        
        if not hay_modelos:
            print("⚠️ No se encontraron modelos de texto. Revisa tu API Key.")
            
    except Exception as e:
        print(f"❌ Error de conexión con Google: {e}")
    
    print("------------------------------------------\n")

if __name__ == "__main__":
    escanear_modelos()