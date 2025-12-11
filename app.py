import streamlit as st
import gspread
import pandas as pd
import json
import re
import ast
import io
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import pypdf
from pptx import Presentation

# --- CONFIGURAZIONE ---
# Modello di default richiesto
DEFAULT_MODEL = "models/gemini-2.5-flash-lite" 

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
if 'available_models' not in st.session_state:
    st.session_state['available_models'] = [DEFAULT_MODEL]
if 'extracted_data' not in st.session_state:
    st.session_state['extracted_data'] = None
if 'search_results' not in st.session_state:
    st.session_state['search_results'] = None

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
def load_data():
    df = pd.DataFrame(ws.get_all_records())
    if not df.empty:
        df.columns = [c.strip() for c in df.columns]
        df.set_index(df.columns[0], inplace=True)
    return df

df = load_data()
if df.empty: st.stop()

product_ids = [str(i) for i in df.index.tolist()]
cols = df.columns.tolist()
id_col = df.index.name

# --- HELPER LETTURA FILE ---
def read_file_content(uploaded_file):
    text = ""
    try:
        if uploaded_file.name.endswith('.pdf'):
            pdf_reader = pypdf.PdfReader(uploaded_file)
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
        elif uploaded_file.name.endswith('.pptx') or uploaded_file.name.endswith('.ppt'):
            prs = Presentation(uploaded_file)
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text += shape.text + "\n"
    except Exception as e:
        st.error(f"Errore lettura file: {e}")
    return text

# --- 3. FUNZIONI AI ---
def analyze_document_with_gemini(text_content, columns, model_name):
    if "GOOGLE_API_KEY" not in st.secrets: return None
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    sys_prompt = f"""
    Sei un esperto data entry. Estrai le informazioni dal documento e mappale ESATTAMENTE nelle seguenti colonne:
    {json.dumps(columns)}
    
    Il campo '{columns[0]}' è il NOME DEL FORMAT.
    OUTPUT: Oggetto JSON valido. Se un dato manca, usa stringa vuota "".
    """

    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        system_instruction=sys_prompt
    )

    try:
        response = model.generate_content(f"DOCUMENTO:\n{text_content}")
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Errore Analisi AI: {e}")
        return None

def search_ai(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    # Inviamo TUTTO il DataFrame
    context_str = dataframe.to_markdown(index=True)

    sys_prompt = """
    Sei un assistente di ricerca. Analizza L'INTERO CATALOGO fornito.
    Output: SOLO lista Python di stringhe (Nomi Format). Es: ['Format A', 'Format B'].
    Se nulla corrisponde: [].
    """
    
    model = genai.GenerativeModel(model_name=model_name, generation_config={"temperature": 0.0}, system_instruction=sys_prompt)
    try:
        response = model.generate_content(f"CATALOGO:\n{context_str}\n\nRICHIESTA: {query}")
        
        if response.usage_metadata:
            st.session_state['token_usage']['input'] += response.usage_metadata.prompt_token_count
            st.session_state['token_usage']['output'] += response.usage_metadata.candidates_token_count
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count

        text = response.text.strip()
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []
    except Exception as e:
        st.error(f"Errore API: {e}")
        return []

# --- INTERFACCIA ---
st.title("🦁 MasterTb Manager")

with st.sidebar:
    st.header("🔢 Token")
    st.metric("Totale", st.session_state['token_usage']['total'])

tab1, tab2, tab3 = st.tabs(["👁️ Cerca & Modifica", "➕ Nuovo Format", "📄 Doc AI (PDF/PPT)"])

# TAB 1: RICERCA
with tab1:
    col_scan, col_sel = st.columns([1, 3])
    
    with col_scan:
        if st.button("🔍 Scansiona Modelli"):
            if "GOOGLE_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
                try:
                    # Filtra modelli che supportano generateContent
                    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    if models:
                        st.session_state['available_models'] = models
                        st.toast(f"Trovati {len(models)} modelli!", icon="✅")
                    else:
                        st.error("Nessun modello trovato.")
                except Exception as e:
                    st.error(f"Errore Scan: {e}")
    
    with col_sel:
        idx_def = 0
        # Cerca di selezionare il default se presente
        if DEFAULT_MODEL in st.session_state['available_models']:
            idx_def = st.session_state['available_models'].index(DEFAULT_MODEL)
            
        selected_model = st.selectbox(
            "Seleziona Modello AI", 
            st.session_state['available_models'],
            index=idx_def
        )

    st.divider()

    with st.form("search_ai"):
        q = st.text_input(f"Cerca {id_col}", placeholder="es. attività outdoor, 50 pax")
        btn = st.form_submit_button("Cerca")
    
    if btn and q:
        with st.spinner("..."):
            res = search_ai(q, df, selected_model)
            if res: 
                valid_ids = [x for x in res if x in product_ids]
                if valid_ids:
                    st.session_state['search_results'] = valid_ids
                else:
                    st.warning("Nessun risultato valido trovato nel DB.")
                    st.session_state['search_results'] = None
            else: 
                st.warning("Nessun risultato dall'AI.")
                st.session_state['search_results'] = None
                
    if st.session_state['search_results'] is not None:
        col_msg, col_rst = st.columns([3, 1])
        count = len(st.session_state['search_results'])
        col_msg.success(f"🦁 Trovati **{count}** format.")
        if col_rst.button("❌ Reset"):
            st.session_state['search_results'] = None
            st.rerun()
        ids_to_show = st.session_state['search_results']
    else:
        ids_to_show = product_ids

    sel = st.selectbox(f"Seleziona {id_col}", ids_to_show)
    
    if sel:
        row = df.loc[sel]
        with st.form("edit"):
            new_vals = {}
            for c in cols:
                v = str(row[c])
                new_vals[c] = st.text_area(c, v) if len(v) > 50 else st.text_input(c, v)
            if st.form_submit_button("Salva"):
                for c, nv in new_vals.items():
                    if str(row[c]) != nv:
                        r = product_ids.index(sel) + 2
                        ci = cols.index(c) + 1
                        ws.update_cell(r, ci, nv)
                st.success("Salvato!")
                load_data.clear()
                st.rerun()

# TAB 2: NUOVO
with tab2:
    with st.form("new"):
        d = {}
        for c in cols:
            d[c] = st.text_input(c) if c == id_col else st.text_area(c)
        if st.form_submit_button("Crea"):
            if d[id_col] in product_ids: st.error("Esiste già")
            else:
                ws.append_row([d[c] for c in cols])
                st.success("Creato!")
                load_data.clear()
                st.rerun()

# TAB 3: DOC AI
with tab3:
    st.header("Caricamento Documenti")
    uploaded_file = st.file_uploader("Carica PDF o PPTX", type=['pdf', 'pptx', 'ppt'])
    
    if uploaded_file:
        if st.button("🦁 Analizza Documento"):
            with st.spinner("Lettura e Analisi con Gemini..."):
                raw_text = read_file_content(uploaded_file)
                if len(raw_text) < 10:
                    st.error("Impossibile leggere il testo dal file.")
                else:
                    extracted = analyze_document_with_gemini(raw_text, [id_col] + cols, selected_model)
                    if extracted:
                        st.session_state['extracted_data'] = extracted
                        st.rerun()
    
    if st.session_state['extracted_data']:
        st.divider()
        st.subheader("Dati Estratti")
        
        data_to_save = {}
        col_id_name = id_col
        
        for col in [col_id_name] + cols:
            val = st.session_state['extracted_data'].get(col, "")
            new_val = st.text_area(f"**{col}**", value=str(val), height=70)
            data_to_save[col] = new_val
        
        st.divider()
        
        current_id_val = data_to_save[col_id_name].strip()
        
        if not current_id_val:
            st.error("⚠️ Il nome del format è vuoto.")
        else:
            if current_id_val in product_ids:
                st.warning(f"⚠️ Il format **'{current_id_val}'** ESISTE GIÀ!")
                
                if st.button("🔄 SOVRASCRIVI i dati esistenti"):
                    try:
                        r_idx = product_ids.index(current_id_val) + 2
                        for i, col_name in enumerate(cols):
                            val_to_write = data_to_save[col_name]
                            ws.update_cell(r_idx, i + 2, val_to_write)
                        st.success(f"Format '{current_id_val}' aggiornato!")
                        st.session_state['extracted_data'] = None
                        load_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Errore aggiornamento: {e}")
            else:
                st.success("✅ Questo format è NUOVO.")
                if st.button("💾 Aggiungi al Foglio"):
                    try:
                        row_to_append = [data_to_save[col_id_name]] + [data_to_save[c] for c in cols]
                        ws.append_row(row_to_append)
                        st.success(f"Format '{current_id_val}' aggiunto!")
                        st.session_state['extracted_data'] = None
                        load_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Errore salvataggio: {e}")

        if st.button("❌ Annulla"):
            st.session_state['extracted_data'] = None
            st.rerun()
