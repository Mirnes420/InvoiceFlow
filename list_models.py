import os
import dotenv
from google import genai

dotenv.load_dotenv()

def list_gemini_models():
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    print(f"{'Model Name':<40} | {'Display Name'}")
    print("-" * 70)

    try:
        # The new SDK uses 'supported_actions'
        for model in client.models.list():
            # 'generateContent' is now usually under supported_actions
            # We will print them all to see exactly what Google gave you
            print(f"{model.name:<40} | {model.display_name}")
            
    except Exception as e:
        print(f"Error fetching models: {e}")
        # If it fails again, let's look at the object structure
        print("\nAttempting raw debug...")
        try:
            m = next(client.models.list())
            print(f"Available attributes on Model object: {dir(m)}")
        except:
            pass

if __name__ == "__main__":
    list_gemini_models()