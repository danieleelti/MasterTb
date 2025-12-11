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

# --- CONFIGURAZIONE MODELLI ---
SEARCH_MODEL = "models/gemini-2.5-flash-lite" # Per la ricerca veloce (Default)
DOC_MODEL = "models/gemini-3-pro-preview"     # Per analisi documenti (Obbligatorio)

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
if 'available_models' not in st.session_state:
    st.session_state['available_models'] = [SEARCH_MODEL]
if 'search_results' not in st.session_state:
    st.session_state['search_results'] = None
# Stato per i dati bozza del nuovo format
if 'draft_data' not in st.session_state:
    st.session_state['draft_data'] = {}

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
def analyze_document_with_gemini(text_content, columns):
    if "GOOGLE_API_KEY" not in st.secrets: return None
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    # Prompt rigoroso per l'analisi documentale
    sys_prompt = f"""
    Sei un esperto data entry per format di team building. 
    Analizza il testo fornito e estrai i dati per compilare le seguenti colonne:
    {json.dumps(columns)}
    
    Il campo '{columns[0]}' è il NOME DEL FORMAT.
    
    REGOLE FONDAMENTALI:
    1. Se trovi l'informazione nel testo, inseriscila.
    2. Se l'informazione NON è presente o non sei sicuro, scrivi ESATTAMENTE: "[[RIEMPIMENTO MANUALE]]".
    3. Non inventare nulla.
    
    OUTPUT: Un oggetto JSON valido.
    """

    # Usa FORZATAMENTE il modello 3.0 Pro Preview per l'analisi
    model = genai.GenerativeModel(
        model_name=DOC_MODEL,
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        system_instruction=sys_prompt
    )

    try:
        response = model.generate_content(f"DOCUMENTO DA ANALIZZARE:\n{text_content}")
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Errore Analisi AI ({DOC_MODEL}): {e}")
        return None

def search_ai(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
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
    st.metric("Totale Sessione", st.session_state['token_usage']['total'])

# TAB UNICI
tab1, tab2 = st.tabs(["👁️ Cerca & Modifica", "➕ Nuovo Format (AI & Manuale)"])

# --- TAB 1: RICERCA ---
with tab1:
    col_scan, col_sel = st.columns([1, 3])
    
    with col_scan:
        if st.button("🔍 Scansiona Modelli"):
            if "GOOGLE_API_KEY" in st.secrets:
                genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
                try:
                    models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    if models:
                        st.session_state['available_models'] = models
                        st.toast(f"Trovati {len(models)} modelli!", icon="✅")
                except Exception as e: st.error(f"Errore Scan: {e}")
    
    with col_sel:
        idx_def = 0
        if SEARCH_MODEL in st.session_state['available_models']:
            idx_def = st.session_state['available_models'].index(SEARCH_MODEL)
        selected_model = st.selectbox("Modello Ricerca", st.session_state['available_models'], index=idx_def)

    st.divider()

    with st.form("search_ai"):
        q = st.text_input(f"Cerca {id_col}", placeholder="es. attività outdoor, 50 pax")
        btn = st.form_submit_button("Cerca")
    
    if btn and q:
        with st.spinner("..."):
            res = search_ai(q, df, selected_model)
            if res: 
                valid_ids = [x for x in res if x in product_ids]
                if valid_ids: st.session_state['search_results'] = valid_ids
                else: 
                    st.warning("Nessun risultato valido.")
                    st.session_state['search_results'] = None
            else: 
                st.warning("Nessun risultato.")
                st.session_state['search_results'] = None
                
    if st.session_state['search_results'] is not None:
        col_msg, col_rst = st.columns([3, 1])
        col_msg.success(f"🦁 Trovati {len(st.session_state['search_results'])} format.")
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

# --- TAB 2: NUOVO FORMAT (AI INTEGRATA) ---
with tab2:
    st.markdown("### 1. Carica Documento (Opzionale)")
    st.info(f"L'analisi documentale userà obbligatoriamente il modello: **{DOC_MODEL}**")
    
    uploaded_file = st.file_uploader("Trascina qui PDF o PPTX per autocompilare", type=['pdf', 'pptx', 'ppt'])
    
    if uploaded_file:
        if st.button("⚡ Estrai Dati con AI"):
            with st.spinner("Analisi in corso (potrebbe richiedere 30-60 secondi)..."):
                raw_text = read_file_content(uploaded_file)
                if len(raw_text) > 10:
                    extracted = analyze_document_with_gemini(raw_text, [id_col] + cols)
                    if extracted:
                        st.session_state['draft_data'] = extracted
                        st.success("Dati estratti! Verifica i campi rossi qui sotto.")
                    else:
                        st.error("L'AI non ha restituito dati validi.")
                else:
                    st.error("Testo insufficiente nel file.")

    st.divider()
    st.markdown("### 2. Dettagli Format")
    
    # Form principale
    with st.form("add_new_format_form"):
        form_values = {}
        missing_fields = []
        
        # Gestione ID (Nome Format)
        id_val = st.session_state['draft_data'].get(id_col, "")
        if id_val == "[[RIEMPIMENTO MANUALE]]":
            st.markdown(f":red[**⚠️ {id_col} MANCANTE - INSERIRE MANUALMENTE**]")
            id_val = "" # Pulisci per l'input
            missing_fields.append(id_col)
            
        new_id = st.text_input(f"**{id_col} (UNICO)** *", value=id_val)
        
        # Gestione Altre Colonne
        for c in cols:
            val = st.session_state['draft_data'].get(c, "")
            
            # Controllo "Riempimento Manuale"
            if "[[RIEMPIMENTO MANUALE]]" in str(val):
                st.markdown(f":red[**⚠️ {c} MANCANTE - COMPLETARE**]")
                val = "" # Pulisci il campo per facilitare l'inserimento
                missing_fields.append(c) # Segna come bloccante
            
            if len(str(val)) > 50:
                form_values[c] = st.text_area(f"**{c}**", value=val)
            else:
                form_values[c] = st.text_input(f"**{c}**", value=val)
        
        submitted = st.form_submit_button("💾 Salva Nuovo Format")
        
        if submitted:
            # 1. Validazione Campi Vuoti o Non Validi
            errors = []
            if not new_id.strip():
                errors.append(f"Il campo '{id_col}' è obbligatorio.")
            
            # Controllo se l'utente ha lasciato campi vuoti che erano segnati come manuali
            # (Opzionale: se vuoi bloccare il salvataggio se un campo è vuoto in assoluto)
            for c, v in form_values.items():
                if not str(v).strip():
                     # Qui decidi: vuoi obbligare a riempire TUTTO?
                     # Se sì, uncommenta la riga sotto. Altrimenti lascia passare i vuoti.
                     errors.append(f"Il campo '{c}' è vuoto.") 
                     pass

            # 2. Controllo Duplicati
            if new_id in product_ids:
                errors.append(f"Il format '{new_id}' esiste già nel database.")

            if errors:
                for e in errors: st.error(e)
                st.error("❌ Salvataggio bloccato. Correggi gli errori sopra.")
            else:
                try:
                    # Preparazione riga
                    row_to_append = [new_id] + [form_values[c] for c in cols]
                    ws.append_row(row_to_append)
                    
                    st.success(f"✅ Format '{new_id}' salvato con successo!")
                    st.balloons()
                    
                    # Pulizia
                    st.session_state['draft_data'] = {}
                    load_data.clear()
                    # Non possiamo fare rerun dentro il form submit in modo pulito senza perdere lo stato del successo, 
                    # ma il reload dei dati è fatto.
                except Exception as e:
                    st.error(f"Errore durante il salvataggio su Google Sheets: {e}")
