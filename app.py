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

# --- CONFIGURAZIONE MODELLI FISSI ---
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
if 'search_results' not in st.session_state:
    st.session_state['search_results'] = None
if 'pending_duplicate' not in st.session_state:
    st.session_state['pending_duplicate'] = None

# Inizializzazione sicura draft_data (Per nuovi upload)
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

    # Identifica colonna descrizione
    desc_col_name = "Descrizione Breve"
    for c in columns:
        if "descrizione" in c.lower():
            desc_col_name = c
            break

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
        model_name=DOC_MODEL, # Fissato a Gemini 3 Pro
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
        return {}

def search_ai(query, dataframe):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    context_str = dataframe.to_markdown(index=True)
    
    # --- PROMPT DI RICERCA SEMANTICA "PENSANTE" ---
    sys_prompt = """
    Sei un Senior Event Manager esperto in Team Building e Formazione Aziendale.
    
    Il tuo compito è analizzare la RICHIESTA dell'utente e trovare nel CATALOGO i format più pertinenti.
    
    ISTRUZIONI DI RAGIONAMENTO (THINKING PROCESS):
    1. Analizza la richiesta dell'utente. Se scrive termini specifici (es. "ponte tibetano"), astrai il concetto (es. "Attività Outdoor", "Adrenalina", "Natura", "Coraggio").
    2. Cerca nel CATALOGO non solo per parole chiave esatte, ma per ASSOCIAZIONE DI IDEE.
       - Es: "Ponte Tibetano" -> Cerca format che contengono "Outdoor", "Adventure Park", "Survival", "Mountain".
       - Es: "Cucina" -> Cerca "Masterchef", "Cooking", "Wine".
    3. Restituisci i NOMI DEI FORMAT (l'ID della prima colonna) che soddisfano meglio la richiesta, anche se la parola esatta non c'è.
    
    Output: SOLO una lista Python di stringhe. Es: ['Nome Format 1', 'Nome Format 2'].
    Nessun altro testo o spiegazione.
    """
    
    model = genai.GenerativeModel(
        model_name=SEARCH_MODEL, # Fissato a Gemini 2.5 Flash
        generation_config={"temperature": 0.1}, # Leggera temp per permettere associazioni creative
        system_instruction=sys_prompt
    )
    try:
        response = model.generate_content(f"CATALOGO:\n{context_str}\n\nRICHIESTA UTENTE: {query}")
        if response.usage_metadata:
            st.session_state['token_usage']['total'] += response.usage_metadata.total_token_count
        
        match = re.search(r"(\[.*\])", response.text.strip(), re.DOTALL)
        return ast.literal_eval(match.group(1)) if match else []
    except: return []

# --- INTERFACCIA PRINCIPALE ---
st.title("🦁 MasterTb Manager")

# 1. AREA UPLOAD (Sempre in cima)
uploaded_file = st.file_uploader("📂 Trascina qui PDF o PPTX per Analizzare/Creare", type=['pdf', 'pptx', 'ppt'])

if uploaded_file:
    if st.button("⚡ Analizza File"):
        with st.spinner("Analisi con Gemini 3.0 Pro..."):
            raw_text = read_file_content(uploaded_file)
            st.session_state['debug_raw_text'] = raw_text 
            
            if len(raw_text) > 10:
                extracted = analyze_document_with_gemini(raw_text, [id_col] + cols)
                
                # --- SAFETY CHECK ---
                if isinstance(extracted, list): extracted = extracted[0] if extracted else {}
                if not isinstance(extracted, dict): extracted = {}

                # --- FUZZY MATCH LOGIC ---
                extracted_name = str(extracted.get(id_col, "")).strip()
                matches = difflib.get_close_matches(extracted_name, product_ids, n=1, cutoff=0.85)
                
                if matches:
                    existing_id = matches[0]
                    st.toast(f"Trovato esistente: {existing_id}", icon="🔄")
                    
                    # Recupera dati e prepara SOLO aggiornamento descrizione
                    current_data = df.loc[existing_id].to_dict()
                    desc_col_name = "Descrizione Breve"
                    for c in cols:
                        if "descrizione" in c.lower():
                            desc_col_name = c
                            break
                    
                    new_desc = extracted.get(desc_col_name, "")
                    
                    st.session_state['pending_duplicate'] = {
                        'id': existing_id,
                        'target_col': desc_col_name,
                        'new_value': new_desc,
                        'old_value': current_data.get(desc_col_name, "")
                    }
                    st.session_state['draft_data'] = {} # Pulisce bozza se è duplicato
                else:
                    # È UN NUOVO FORMAT
                    st.session_state['pending_duplicate'] = None
                    st.session_state['draft_data'] = extracted if extracted else {}
                    if st.session_state['draft_data']:
                        st.toast("Dati estratti per NUOVO format!", icon="✨")
            else:
                st.error("Testo insufficiente nel file.")

# INTERVENTO DUPLICATI (Box Giallo)
if st.session_state['pending_duplicate']:
    st.divider()
    dup_data = st.session_state['pending_duplicate']
    dup_id = dup_data['id']
    target_col = dup_data.get('target_col', "Descrizione Breve")
    new_val = dup_data.get('new_value', "")
    old_val = dup_data.get('old_value', "")

    st.warning(f"⚠️ **ATTENZIONE:** Il format **'{dup_id}'** esiste già!")
    st.markdown(f"**L'AI propone di aggiornare SOLO la colonna '{target_col}'**.")
    
    col_diff1, col_diff2 = st.columns(2)
    with col_diff1:
        st.caption("🔴 Descrizione Attuale")
        st.info(old_val if old_val else "(Vuoto)", icon="ℹ️")
    with col_diff2:
        st.caption("🟢 Nuova Descrizione (AI)")
        st.success(new_val, icon="✨")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 AGGIORNA SOLO DESCRIZIONE", type="primary"):
            try:
                r_idx = product_ids.index(dup_id) + 2 
                c_idx = cols.index(target_col) + 2
                ws.update_cell(r_idx, c_idx, new_val)
                st.toast("Aggiornato!", icon="✅")
                st.session_state['pending_duplicate'] = None
                load_data.clear()
                st.rerun()
            except Exception as e: st.error(f"Errore: {e}")
    with c2:
        if st.button("❌ ANNULLA"):
            st.session_state['pending_duplicate'] = None
            st.rerun()
    st.divider()

# 2. CAMPO CERCA (AI)
st.markdown("### 🔎 Ricerca e Selezione")
col_search, col_rst = st.columns([4, 1])
with col_search:
    q = st.text_input("Cerca Format (per contenuto, idea o nome)", placeholder="Es. ponte tibetano, cucina, investigazione...")
with col_rst:
    st.write("")
    st.write("")
    if st.button("Vai"):
        if q:
            with st.spinner("Cerco format pertinenti..."):
                res = search_ai(q, df)
                valid_ids = [x for x in res if x in product_ids] if res else []
                st.session_state['search_results'] = valid_ids if valid_ids else None
                if not valid_ids: st.warning("Nessun risultato pertinente trovato.")

# Gestione opzioni selectbox
if st.session_state['search_results'] is not None:
    options = st.session_state['search_results']
    st.info(f"Filtro AI attivo: {len(options)} format trovati per '{q}'.")
    if st.button("Mostra Tutti"):
        st.session_state['search_results'] = None
        st.rerun()
else:
    options = product_ids

# 3. SELEZIONE NOME FORMAT
# Se c'è un draft_data (Nuovo format da upload), non selezioniamo nulla dal DB ma mostriamo quello
is_new_mode = False
if st.session_state['draft_data'] and not st.session_state['pending_duplicate']:
    is_new_mode = True
    st.info("✏️ **MODALITÀ CREAZIONE**: Stai modificando i dati estratti dal file caricato.")
    if st.button("🔙 Annulla Creazione"):
        st.session_state['draft_data'] = {}
        st.rerun()
else:
    selected_id = st.selectbox("Seleziona Format da Modificare", options)

# 4. TABELLA (FORM)
st.markdown("### 📝 Dettagli Format")

with st.form("master_form"):
    form_values = {}
    
    # DETERMINA LA SORGENTE DATI
    if is_new_mode:
        # Sorgente: Dati AI (Draft)
        source_data = st.session_state['draft_data']
        current_id_val = str(source_data.get(id_col, ""))
        submit_label = "💾 SALVA NUOVO FORMAT"
    else:
        # Sorgente: Google Sheet (Row selezionata)
        if selected_id:
            source_data = df.loc[selected_id].to_dict()
            current_id_val = selected_id
            submit_label = "💾 SALVA MODIFICHE"
        else:
            st.warning("Seleziona un format o carica un file.")
            st.form_submit_button("...")
            st.stop()

    # RENDERIZZA CAMPI
    # Gestione ID (Chiave primaria)
    if is_new_mode:
        new_id = st.text_input(f"**{id_col} (UNICO)**", value=current_id_val)
    else:
        # In edit mode l'ID non si tocca per non rompere il database
        st.text_input(f"**{id_col}**", value=current_id_val, disabled=True)
        new_id = current_id_val

    # Loop sulle colonne
    for c in cols:
        val = str(source_data.get(c, ""))
        
        # Pulizia placeholder AI
        if "[[RIEMPIMENTO MANUALE]]" in val: val = ""
        
        # Altezza dinamica per descrizioni
        height = 150 if "descrizione" in c.lower() else 0
        
        if len(val) > 50 or height > 0:
            form_values[c] = st.text_area(f"**{c}**", value=val, height=height if height else None)
        else:
            form_values[c] = st.text_input(f"**{c}**", value=val)

    # SUBMIT
    submitted = st.form_submit_button(submit_label, type="primary")

    if submitted:
        if is_new_mode:
            # --- SALVATAGGIO NUOVO ---
            if not new_id.strip():
                st.error(f"Il campo {id_col} è obbligatorio.")
            elif new_id in product_ids:
                st.error("Esiste già un format con questo nome! Cambia nome.")
            else:
                try:
                    row_to_append = [new_id] + [form_values[c] for c in cols]
                    ws.append_row(row_to_append)
                    st.success(f"Nuovo format '{new_id}' creato!")
                    st.session_state['draft_data'] = {}
                    load_data.clear()
                    st.rerun()
                except Exception as e: st.error(f"Errore salvataggio: {e}")
        else:
            # --- AGGIORNAMENTO ESISTENTE ---
            # Aggiorniamo solo le celle cambiate
            updates_count = 0
            try:
                # Indici foglio: Row parte da 2 (1 headers), Col parte da 2 (1 ID)
                row_idx = product_ids.index(selected_id) + 2
                
                for col_name, new_val in form_values.items():
                    old_val = str(source_data.get(col_name, ""))
                    if old_val != new_val:
                        col_idx = cols.index(col_name) + 2
                        ws.update_cell(row_idx, col_idx, new_val)
                        updates_count += 1
                
                if updates_count > 0:
                    st.success(f"Salvato! Aggiornati {updates_count} campi.")
                    load_data.clear()
                    st.rerun()
                else:
                    st.info("Nessuna modifica rilevata.")
            except Exception as e: st.error(f"Errore aggiornamento: {e}")
