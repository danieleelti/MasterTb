import streamlit as st
import streamlit.components.v1 as components
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
from pptx.enum.shapes import MSO_SHAPE_TYPE
import difflib

# --- CONFIGURAZIONE ---
SEARCH_MODEL = "models/gemini-2.5-flash-lite"
DOC_MODEL = "models/gemini-3-pro-preview"

# --- CSS E JS PER DRAG & DROP VISUALE ---
components.html("""
<script>
    const observer = new MutationObserver(() => {
        const dropzone = window.parent.document.querySelector('[data-testid="stFileUploaderDropzone"]');
        if (dropzone && !dropzone.classList.contains('drag-listener')) {
            dropzone.classList.add('drag-listener');
            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropzone.style.border = '3px dashed #FF4B4B';
                dropzone.style.backgroundColor = '#FFECEC';
                dropzone.style.transform = 'scale(1.02)';
            });
            const reset = () => {
                dropzone.style.border = '1px dashed #aaa';
                dropzone.style.backgroundColor = '#f9f9f9';
                dropzone.style.transform = 'scale(1.0)';
            };
            dropzone.addEventListener('dragleave', reset);
            dropzone.addEventListener('drop', reset);
        }
    });
    observer.observe(window.parent.document.body, { childList: true, subtree: true });
</script>
""", height=0, width=0)

st.markdown("""
    <style>
    [data-testid='stFileUploaderDropzone'] {
        border: 1px dashed #aaa;
        border-radius: 10px;
        background-color: #f9f9f9;
        transition: all 0.2s ease-in-out;
    }
    </style>
""", unsafe_allow_html=True)

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

# Inizializzazione sicura draft_data
if 'draft_data' not in st.session_state:
    st.session_state['draft_data'] = {}
elif st.session_state['draft_data'] is None:
    st.session_state['draft_data'] = {}

# Variabili Debug
if 'debug_raw_text' not in st.session_state: st.session_state['debug_raw_text'] = ""
if 'debug_ai_response' not in st.session_state: st.session_state['debug_ai_response'] = ""

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
def get_shape_text_recursive(shape):
    text = ""
    try:
        if hasattr(shape, "has_text_frame") and shape.has_text_frame:
            text += shape.text_frame.text + "\n"
        if hasattr(shape, "has_table") and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    if hasattr(cell, "text_frame"):
                        text += cell.text_frame.text + " "
            text += "\n"
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for child in shape.shapes:
                text += get_shape_text_recursive(child)
    except: pass
    return text

def read_file_content(uploaded_file):
    text = ""
    try:
        if uploaded_file.name.endswith('.pdf'):
            pdf_reader = pypdf.PdfReader(uploaded_file)
            for page in pdf_reader.pages:
                extracted = page.extract_text()
                if extracted: text += extracted + "\n"
        elif uploaded_file.name.endswith('.pptx') or uploaded_file.name.endswith('.ppt'):
            prs = Presentation(uploaded_file)
            for slide in prs.slides:
                for shape in slide.shapes:
                    text += get_shape_text_recursive(shape)
    except Exception as e:
        st.error(f"Errore lettura file: {e}")
    return text

# --- 3. FUNZIONI AI ---
def analyze_document_with_gemini(text_content, columns):
    if "GOOGLE_API_KEY" not in st.secrets: return {}
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    # Identifica colonna descrizione per prompt specifico
    desc_col_name = "Descrizione Breve"
    for c in columns:
        if "descrizione" in c.lower():
            desc_col_name = c
            break

    # Prompt aggiornato per richiedere più testo nella descrizione
    sys_prompt = f"""
    Sei un esperto copywriter e data entry. Analizza il testo fornito.
    
    OBIETTIVO: Compilare un JSON con queste chiavi esatte:
    {json.dumps(columns)}
    
    1. Campo Chiave: '{columns[0]}' (NOME FORMAT).
    2. Campo '{desc_col_name}': QUESTO È IL CAMPO PIÙ IMPORTANTE. Devi scrivere un paragrafo discorsivo di ALMENO 5-6 RIGHE COMPLETE. Descrivi l'attività in modo coinvolgente, spiegando cosa si fa, gli obiettivi e l'atmosfera. Non usare elenchi puntati qui, ma testo scorrevole.
    
    REGOLE GENERALI:
    - Se trovi l'informazione, scrivila.
    - Se l'informazione MANCA DEL TUTTO, scrivi ESATTAMENTE: "[[RIEMPIMENTO MANUALE]]".
    - Rispondi SOLO con il JSON.
    """

    model = genai.GenerativeModel(
        model_name=DOC_MODEL,
        generation_config={"temperature": 0.2, "response_mime_type": "application/json"},
        system_instruction=sys_prompt
    )

    try:
        response = model.generate_content(f"TESTO DOCUMENTO:\n{text_content}")
        clean_text = response.text.strip()
        if clean_text.startswith("```json"): clean_text = clean_text[7:]
        if clean_text.endswith("```"): clean_text = clean_text[:-3]
        
        st.session_state['debug_ai_response'] = clean_text 
        return json.loads(clean_text.strip())
    except Exception as e:
        st.error(f"Errore AI ({DOC_MODEL}): {e}")
        st.session_state['debug_ai_response'] = str(e)
        return {}

def search_ai(query, dataframe, model_name):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    context_str = dataframe.to_markdown(index=True)
    sys_prompt = "Sei un assistente di ricerca. Output: SOLO lista Python Nomi Format. Es: ['Nome A']."
    
    model = genai.GenerativeModel(model_name=model_name, generation_config={"temperature": 0.0}, system_instruction=sys_prompt)
    try:
        response = model.generate_content(f"CATALOGO:\n{context_str}\n\nRICHIESTA: {query}")
        if response.usage_metadata:
            st.session_state['token_usage']['input'] += response.usage_metadata.prompt_token_count
            st.session_state['token_usage']['output'] += response.usage_metadata.candidates_token_count
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count
        
        match = re.search(r"(\[.*\])", response.text.strip(), re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []
    except: return []

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
                st.session_state['search_results'] = valid_ids if valid_ids else None
                if not valid_ids: st.warning("Nessun risultato valido.")
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
                        ci = cols.index(c) + 2 # Corretto: Cols è lista headers senza ID, sheet parte da 1.
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
                st.session_state['debug_raw_text'] = raw_text 
                
                if len(raw_text) > 10:
                    extracted = analyze_document_with_gemini(raw_text, [id_col] + cols)
                    
                    # --- FUZZY MATCH LOGIC ---
                    extracted_name = str(extracted.get(id_col, "")).strip()
                    matches = difflib.get_close_matches(extracted_name, product_ids, n=1, cutoff=0.85)
                    
                    if matches:
                        existing_id = matches[0]
                        st.toast(f"⚠️ Trovato format simile: {existing_id}", icon="🔄")
                        
                        # 1. Recupera i dati ATTUALI dal Google Sheet (tramite il DF caricato)
                        current_data = df.loc[existing_id].to_dict()
                        
                        # 2. Identifica la colonna descrizione
                        desc_col_name = "Descrizione Breve" # Fallback
                        for c in cols:
                            if "descrizione" in c.lower():
                                desc_col_name = c
                                break
                        
                        # 3. Prepara il pacchetto per l'aggiornamento
                        # Manteniamo TUTTO uguale, cambiamo solo la descrizione con quella dell'AI
                        new_desc = extracted.get(desc_col_name, "")
                        
                        st.session_state['pending_duplicate'] = {
                            'id': existing_id,
                            'target_col': desc_col_name,
                            'new_value': new_desc,
                            'old_value': current_data.get(desc_col_name, "")
                        }
                    else:
                        st.session_state['pending_duplicate'] = None
                        # Solo se NON è un duplicato salviamo i dati draft per il form di creazione
                        st.session_state['draft_data'] = extracted if extracted else {}

                    # Logica messaggi
                    if st.session_state['pending_duplicate']:
                        st.warning(f"Format esistente rilevato: {matches[0]}. Vedi opzioni sotto.")
                    elif st.session_state['draft_data']:
                        st.success("Dati estratti! Verifica i campi sotto.")
                    else:
                        st.error("L'AI non ha estratto dati validi.")
                else:
                    st.error("Testo insufficiente nel file.")

    # DEBUG
    if st.session_state.get('debug_raw_text'):
        with st.expander("🕵️‍♂️ DEBUG: Vedi cosa ha letto il sistema"):
            st.text(st.session_state['debug_raw_text'][:2000])
            st.divider()
            st.code(st.session_state.get('debug_ai_response', 'Nessuna risposta'))

    st.divider()
    st.markdown("### 2. Dettagli Format")
    
    # BOX DI SCELTA (Appare se c'è un pending duplicate)
    if st.session_state['pending_duplicate']:
        dup_data = st.session_state['pending_duplicate']
        dup_id = dup_data['id']
        target_col = dup_data.get('target_col', "Descrizione Breve")
        new_val = dup_data.get('new_value', "")
        old_val = dup_data.get('old_value', "")

        st.warning(f"⚠️ **ATTENZIONE:** Il format **'{dup_id}'** esiste già!")
        
        st.markdown(f"**L'AI propone di aggiornare SOLO la colonna '{target_col}'** (mantenendo invariati gli altri dati).")
        
        col_diff1, col_diff2 = st.columns(2)
        with col_diff1:
            st.caption("🔴 Descrizione Attuale")
            st.info(old_val, icon="ℹ️")
        with col_diff2:
            st.caption("🟢 Nuova Descrizione (AI)")
            st.success(new_val, icon="✨")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 AGGIORNA SOLO DESCRIZIONE", type="primary"):
                try:
                    # Trova coordinate
                    r_idx = product_ids.index(dup_id) + 2 # +2 headers e 0-index
                    c_idx = cols.index(target_col) + 2 # +2 (ID è col 1)
                    
                    # Aggiorna SOLO quella cella
                    ws.update_cell(r_idx, c_idx, new_val)
                    
                    st.toast(f"Descrizione aggiornata per {dup_id}!", icon="✅")
                    st.session_state['pending_duplicate'] = None
                    st.session_state['draft_data'] = {}
                    load_data.clear()
                    st.rerun()
                except Exception as e: st.error(f"Errore aggiornamento cella: {e}")
        with c2:
            if st.button("❌ ANNULLA (Ignora aggiornamento)"):
                st.session_state['pending_duplicate'] = None
                st.session_state['draft_data'] = {} 
                st.rerun()
        st.divider()

    # FORM
    with st.form("add_new_format_form"):
        form_values = {}
        missing_fields = []
        
        draft = st.session_state.get('draft_data')
        if not isinstance(draft, dict): draft = {}
        
        id_val = str(draft.get(id_col, ""))
        if id_val == "[[RIEMPIMENTO MANUALE]]":
            st.markdown(f":red[**⚠️ {id_col} MANCANTE**]")
            id_val = ""
            missing_fields.append(id_col)
            
        new_id = st.text_input(f"**{id_col} (UNICO)** *", value=id_val)
        
        for c in cols:
            val = str(draft.get(c, ""))
            if "[[RIEMPIMENTO MANUALE]]" in val:
                st.markdown(f":red[**⚠️ {c} MANCANTE**]")
                val = ""
                missing_fields.append(c)
            
            # Text area più alta per la descrizione
            height = 150 if "descrizione" in c.lower() else 0
            
            if len(val) > 50 or height > 0:
                form_values[c] = st.text_area(f"**{c}**", value=val, height=height if height else None)
            else:
                form_values[c] = st.text_input(f"**{c}**", value=val)
        
        submitted = st.form_submit_button("💾 Salva Nuovo Format")
        
        if submitted:
            errors = []
            if not new_id.strip(): errors.append(f"Manca {id_col}")
            
            if errors:
                for e in errors: st.error(e)
            else:
                # Controllo duplicato manuale (se l'utente cambia nome a mano)
                if new_id in product_ids:
                    st.session_state['pending_duplicate'] = {
                        'id': new_id,
                        'values': form_values # Fallback per manuale
                    }
                    st.rerun()
                else:
                    try:
                        row_to_append = [new_id] + [form_values[c] for c in cols]
                        ws.append_row(row_to_append)
                        st.success(f"Salvato!")
                        st.session_state['draft_data'] = {}
                        load_data.clear()
                    except Exception as e:
                        st.error(f"Errore: {e}")
