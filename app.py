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
SEARCH_MODEL = "models/gemini-2.5-flash-lite"
DOC_MODEL = "models/gemini-3-pro-preview"

# --- INIZIALIZZAZIONE STATO ---
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'token_usage' not in st.session_state: 
    st.session_state['token_usage'] = {'input': 0, 'output': 0, 'total': 0}
if 'available_models' not in st.session_state:
    st.session_state['available_models'] = [SEARCH_MODEL]
if 'search_results' not in st.session_state:
    st.session_state['search_results'] = None
if 'pending_duplicate' not in st.session_state:
    st.session_state['pending_duplicate'] = None

# FIX CRITICO: Inizializzazione sicura
if 'draft_data' not in st.session_state:
    st.session_state['draft_data'] = {}
elif st.session_state['draft_data'] is None:
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
    if "GOOGLE_API_KEY" not in st.secrets: return {}
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    sys_prompt = f"""
    Sei un esperto data entry. Estrai dati dal documento per queste colonne:
    {json.dumps(columns)}
    
    Campo chiave: '{columns[0]}' (NOME FORMAT).
    
    REGOLE:
    1. Se il dato manca, scrivi ESATTAMENTE: "[[RIEMPIMENTO MANUALE]]".
    2. Non inventare.
    
    OUTPUT: Solo JSON valido.
    """

    model = genai.GenerativeModel(
        model_name=DOC_MODEL,
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        system_instruction=sys_prompt
    )

    try:
        response = model.generate_content(f"DOCUMENTO:\n{text_content}")
        # Pulizia robusta del JSON
        clean_text = response.text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        
        return json.loads(clean_text.strip())
    except Exception as e:
        st.error(f"Errore Analisi AI ({DOC_MODEL}): {e}")
        return {}

def search_ai(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    context_str = dataframe.to_markdown(index=True)

    sys_prompt = """
    Sei un assistente di ricerca. Analizza il catalogo.
    Output: SOLO lista Python di stringhe (Nomi Format). Es: ['Format A'].
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
                        st.toast(f"Trovati {len(models)}!", icon="✅")
                except Exception as e: st.error(f"Scan Error: {e}")
    
    with col_sel:
        idx_def = 0
        if SEARCH_MODEL in st.session_state['available_models']:
            idx_def = st.session_state['available_models'].index(SEARCH_MODEL)
        selected_model = st.selectbox("Modello Ricerca", st.session_state['available_models'], index=idx_def)

    st.divider()

    with st.form("search_ai"):
        q = st.text_input(f"Cerca {id_col}", placeholder="es. attività outdoor")
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

# --- TAB 2: NUOVO FORMAT ---
with tab2:
    st.markdown("### 1. Carica Documento")
    st.info(f"Analisi con: **{DOC_MODEL}**")
    
    uploaded_file = st.file_uploader("Trascina qui PDF o PPTX", type=['pdf', 'pptx', 'ppt'])
    
    if uploaded_file:
        if st.button("⚡ Estrai Dati"):
            with st.spinner("Analisi in corso..."):
                raw_text = read_file_content(uploaded_file)
                if len(raw_text) > 10:
                    extracted = analyze_document_with_gemini(raw_text, [id_col] + cols)
                    st.session_state['draft_data'] = extracted if extracted is not None else {}
                    st.session_state['pending_duplicate'] = None 
                    
                    if st.session_state['draft_data']:
                        st.success("Dati estratti! Compila i campi sottostanti.")
                        # RIMOSSO st.rerun() PER EVITARE IL CAMBIO TAB
                    else:
                        st.error("L'AI non ha estratto dati validi.")
                else:
                    st.error("Testo insufficiente.")

    st.divider()
    st.markdown("### 2. Dettagli Format")
    
    # --- GESTIONE DECISIONALE DUPLICATI ---
    if st.session_state['pending_duplicate']:
        dup_data = st.session_state['pending_duplicate']
        dup_id = dup_data['id']
        st.warning(f"⚠️ **ATTENZIONE:** Il format **'{dup_id}'** esiste già nel database!")
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 AGGIORNA ESISTENTE", type="primary"):
                try:
                    r_idx = product_ids.index(dup_id) + 2
                    for i, col in enumerate(cols):
                        val = dup_data['values'][col]
                        ws.update_cell(r_idx, i + 2, val)
                    st.success(f"Format '{dup_id}' aggiornato!")
                    st.session_state['pending_duplicate'] = None
                    st.session_state['draft_data'] = {}
                    load_data.clear()
                    st.rerun()
                except Exception as e: st.error(f"Errore: {e}")
        
        with c2:
            if st.button("❌ ANNULLA OPERAZIONE"):
                st.session_state['pending_duplicate'] = None
                st.rerun()
                
        st.divider()

    # --- FORM PRINCIPALE ---
    with st.form("add_new_format_form"):
        form_values = {}
        missing_fields = []
        
        draft = st.session_state.get('draft_data')
        if not isinstance(draft, dict): draft = {}
        
        id_val = draft.get(id_col, "")
        if id_val == "[[RIEMPIMENTO MANUALE]]":
            st.markdown(f":red[**⚠️ {id_col} MANCANTE**]")
            id_val = ""
            missing_fields.append(id_col)
            
        new_id = st.text_input(f"**{id_col} (UNICO)** *", value=id_val)
        
        for c in cols:
            val = draft.get(c, "")
            if "[[RIEMPIMENTO MANUALE]]" in str(val):
                st.markdown(f":red[**⚠️ {c} MANCANTE**]")
                val = ""
                missing_fields.append(c)
            
            if len(str(val)) > 50:
                form_values[c] = st.text_area(f"**{c}**", value=val)
            else:
                form_values[c] = st.text_input(f"**{c}**", value=val)
        
        submitted = st.form_submit_button("💾 Salva Nuovo Format")
        
        if submitted:
            errors = []
            if not new_id.strip(): errors.append(f"Manca {id_col}")
            
            if errors:
                for e in errors: st.error(e)
            else:
                if new_id in product_ids:
                    st.session_state['pending_duplicate'] = {
                        'id': new_id,
                        'values': form_values
                    }
                    st.rerun()
                else:
                    try:
                        row_to_append = [new_id] + [form_values[c] for c in cols]
                        ws.append_row(row_to_append)
                        st.success(f"✅ Format '{new_id}' salvato!")
                        st.session_state['draft_data'] = {}
                        load_data.clear()
                    except Exception as e:
                        st.error(f"Errore salvataggio: {e}")
