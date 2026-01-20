from youtube_transcript_api import YouTubeTranscriptApi
import re
import sys

def obtener_transcripcion_oficial(url_o_id):
    # Extraer el ID
    video_id = url_o_id
    if "v=" in url_o_id:
        video_id = re.search(r"v=([^&]+)", url_o_id).group(1)
    elif "youtu.be/" in url_o_id:
        video_id = url_o_id.split("/")[-1]

    print(f"--- Procesando ID: {video_id} ---")

    try:
        # Usamos la instancia que confirmaste que funciona
        ytt_api = YouTubeTranscriptApi()
        
        # .fetch() con prioridad de idiomas
        fetched_transcript = ytt_api.fetch(video_id, languages=['es', 'en'])
        
        # Extraemos el texto
        texto_final = " ".join([snippet.text for snippet in fetched_transcript])
        
        # Guardamos con nombre fijo para que el YAML lo encuentre siempre
        with open("resultado.txt", "w", encoding="utf-8") as f:
            f.write(texto_final)
            
        print(f"✅ ¡ÉXITO!")
        return True

    except Exception as e:
        print(f"❌ FALLÓ: {str(e)}")
        sys.exit(1) # Forzamos error para que GitHub avise si falló

if __name__ == "__main__":
    # Puedes cambiar este ID por el que necesites
    video_target = "mogwWvsHrpg" 
    obtener_transcripcion_oficial(video_target)