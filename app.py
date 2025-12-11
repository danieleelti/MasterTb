import streamlit as st
import gspread
import pandas as pd
import json
import re
import ast
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- CONFIGURAZIONE INIZIALE ---
# Questo è il nome che proveremo a usare di default.
# Se fallisce, usa il tool nella sidebar per trovare il nome vero.
DEFAULT_MODEL_NAME = 'models/gemini-1.5-flash' 

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}

# --- 1. LOGIN ---
if not st.session_state['logged_in']:
    st.title("🦁 MasterTb Accesso")
    with st.form("login"):
        pwd = st.text_input("Password", type="password")
        if st.form_submit_button("Entra"):
            if "login_password" in st.secrets and pwd == st.secrets["login_password"]:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Password errata")
    st.stop()

# --- 2. CONNESSIONE ---
ws = None
@st.cache_resource
def connect_to_sheet():
    try:
        creds = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(creds)
        return gc.open("MasterTbGoogleAi").get_worksheet(0)
    except Exception as e:
        st.error(f"Errore Sheet: {e}")
        st.stop()

ws = connect_to_sheet()

@st.cache_data(ttl=60)
def get_data():
    df = pd.DataFrame(ws.get_all_records())
    if not df.empty:
        df.columns = [c.strip() for c in df.columns]
        df.set_index(df.columns[0], inplace=True)
    return df

df = get_data()
if df.empty: st.stop()

product_ids = [str(i) for i in df.index.tolist()]
cols = df.columns.tolist()
id_col = df.index.name

# --- 3. TOOL DIAGNOSTICO (SIDEBAR) ---
with st.sidebar:
    st.header("🛠️ Diagnostica API")
    
    # Configura API Key globalmente per il test
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        
        if st.button("🔍 Scansiona Modelli Disponibili"):
            try:
                st.write("Interrogazione Google in corso...")
                # Elenca solo i modelli che supportano 'generateContent'
                models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                st.success(f"Trovati {len(models)} modelli!")
                st.code("\n".join(models)) # Copia uno di questi nomi!
            except Exception as e:
                st.error(f"Errore Scansione: {e}")
    else:
        st.error("Manca API Key nei secrets")

    st.markdown("---")
    st.header("🔢 Token Sessione")
    st.metric("Input", st.session_state['token_usage']['input'])
    st.metric("Output", st.session_state['token_usage']['output'])
    
    if st.button("Reset Token"):
        st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
        st.rerun()

# --- 4. FUNZIONE AI (Il tuo codice) ---
def search_ai(query, dataframe, model_name):
    try:
        # Preparazione Dati (Col 1 + Col 16)
        all_cols = list(dataframe.columns)
        # Indice 15 = Colonna 16 (Descrizione Breve)
        target_col = all_cols[15] if len(all_cols) > 15 else all_cols[0]
        
        # Creiamo un subset leggero
        sub_df = dataframe[[target_col]]
        context_str = sub_df.to_markdown(index=True)

        system_prompt = """
        Sei un assistente di ricerca specializzato in format di team building.
        Analizza la richiesta e il catalogo fornito (Nome Format + Descrizione).
        Output: SOLO una lista Python di stringhe dei Nomi Format trovati. Es: ['Format A'].
        Se nulla corrisponde: [].
        """

        # Configurazione Modello (Tuo snippet esatto)
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={"temperature": 0.0},
            system_instruction=system_prompt,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        full_prompt = f"CATALOGO:\n{context_str}\n\nRICHIESTA UTENTE: {query}"
        
        response = model.generate_content(full_prompt)
        
        # Conteggio
        if response.usage_metadata:
            st.session_state['token_usage']['input'] += response.usage_metadata.prompt_token_count
            st.session_state['token_usage']['output'] += response.usage_metadata.candidates_token_count
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count

        text = response.text.strip()
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []

    except Exception as e:
        # Mostra l'errore nudo e crudo per debug
        st.error(f"Errore API ({model_name}): {e}")
        return []

# --- 5. INTERFACCIA PRINCIPALE ---
st.title("🦁 MasterTb Manager")

tab1, tab2, tab3 = st.tabs(["👁️ Cerca & Modifica", "➕ Nuovo Format", "📄 Doc AI"])

with tab1:
    # Selettore manuale per testare i nomi trovati con la diagnostica
    # Ho messo 'gemini-3-pro-preview' come richiesto, ma puoi cambiarlo al volo se fallisce
    model_to_use = st.text_input("Nome Modello (Copia dalla Sidebar)", value="gemini-1.5-flash")
    
    with st.form("search_form"):
        q = st.text_input(f"Cerca {id_col}", placeholder="es. avventura outdoor")
        btn = st.form_submit_button("Cerca con Gemini 🦁")
    
    ids_show = product_ids
    if btn and q:
        with st.spinner("Ragionando..."):
            res = search_ai(q, df, model_to_use)
            if res:
                ids_show = [x for x in res if x in product_ids]
                if not ids_show: st.warning("Nessun match esatto nel DB.")
            else:
                st.warning("Nessun risultato.")

    sel = st.selectbox(f"Seleziona {id_col}", ids_show)
    
    if sel:
        row = df.loc[sel]
        with st.form("edit"):
            st.subheader(f"Modifica: {sel}")
            new_vals = {}
            for c in cols:
                v = str(row[c])
                if len(v) > 50: new_vals[c] = st.text_area(c, v)
                else: new_vals[c] = st.text_input(c, v)
            
            if st.form_submit_button("Salva Modifiche"):
                for c, nv in new_vals.items():
                    if str(row[c]) != nv:
                        # Update logic
                        r = product_ids.index(sel) + 2
                        ci = cols.index(c) + 1
                        ws.update_cell(r, ci, nv)
                        st.toast(f"Aggiornato {c}")
                st.rerun()

with tab2:
    st.header("Nuovo")
    with st.form("new"):
        d = {}
        for c in cols:
            d[c] = st.text_input(c) if c == id_col else st.text_area(c)
        if st.form_submit_button("Crea"):
            if d[id_col] in product_ids: st.error("Esiste già")
            else:
                ws.append_row([d[c] for c in cols])
                st.success("Fatto")
                st.rerun()

with tab3:
    st.info("Area PDF pronta.")
