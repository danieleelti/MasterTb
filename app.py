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

# --- HELPER UTILITY ---
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

def create_slug(text):
    """Crea uno slug valido per l'URL dal nome del format."""
    if not text: return ""
    # Rimuove caratteri speciali, mette tutto lowercase, sostituisce spazi con -
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return text

# --- 3. FUNZIONI AI ---
def analyze_document_with_gemini(text_content, columns):
    if "GOOGLE_API_KEY" not in st.secrets: return {}
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    desc_col_name = "Descrizione Breve"
    for c in columns:
        if "descrizione" in c.lower():
            desc_col_name = c
            break

    # Prompt aggiornato con REGOLE DI FORMATTAZIONE SPECIFICHE
    sys_prompt = f"""
    Sei un esperto copywriter e data entry. Analizza il testo fornito.
    
    OBIETTIVO: Compilare un JSON con queste chiavi esatte:
    {json.dumps(columns)}
    
    1. Campo Chiave: '{columns[0]}' (NOME FORMAT).
    2. Campo '{desc_col_name}': Scrivi un paragrafo discorsivo di ALMENO 5-6 RIGHE COMPLETE. Descrivi l'attività in modo coinvolgente.
    
    REGOLE SPECIFICHE PER I CAMPI:
    - Campo 'Social': Se l'attività prevede foto/video o condivisione, scrivi "SI", altrimenti "NO".
    - Campo 'Ranking': Valuta l'intensità/complessità da 1 a 5. Scrivi solo il numero intero (es. 3).
    - Campo 'Durata Ideale': Se trovi un range (es. 2-4 ore), calcola la MEDIA (es. 3). Scrivi solo il numero o la media.
    - Se l'informazione MANCA DEL TUTTO, scrivi "[[RIEMPIMENTO MANUALE]]".
    
    Rispondi SOLO con il JSON.
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
        return {}

def search_ai(query, dataframe):
    if "GOOGLE_API_KEY" not in st.secrets: return []
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    context_str = dataframe.to_markdown(index=True)
    
    sys_prompt = """
    Sei un Senior Event Manager esperto in Team Building.
    
    Analizza la RICHIESTA dell'utente e trova nel CATALOGO i format più pertinenti.
    
    THINKING PROCESS:
    1. Astrai la richiesta (es. "ponte tibetano" -> "Outdoor/Avventura").
    2. Cerca per ASSOCIAZIONE DI IDEE, non solo keyword esatte.
    3. Restituisci i NOMI DEI FORMAT (ID colonna 1).
    
    Output: SOLO lista Python. Es: ['Format A', 'Format B'].
    """
    
    model = genai.GenerativeModel(
        model_name=SEARCH_MODEL,
        generation_config={"temperature": 0.1},
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

# 1. AREA UPLOAD
uploaded_file = st.file_uploader("📂 Trascina qui PDF o PPTX per Analizzare/Creare", type=['pdf', 'pptx', 'ppt'])

if uploaded_file:
    if st.button("⚡ Analizza File"):
        with st.spinner("Analisi con Gemini 3.0 Pro..."):
            raw_text = read_file_content(uploaded_file)
            st.session_state['debug_raw_text'] = raw_text 
            
            if len(raw_text) > 10:
                extracted = analyze_document_with_gemini(raw_text, [id_col] + cols)
                
                if isinstance(extracted, list): extracted = extracted[0] if extracted else {}
                if not isinstance(extracted, dict): extracted = {}

                # FUZZY MATCH
                extracted_name = str(extracted.get(id_col, "")).strip()
                matches = difflib.get_close_matches(extracted_name, product_ids, n=1, cutoff=0.85)
                
                if matches:
                    existing_id = matches[0]
                    st.toast(f"Trovato esistente: {existing_id}", icon="🔄")
                    
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
                    st.session_state['draft_data'] = {}
                else:
                    st.session_state['pending_duplicate'] = None
                    st.session_state['draft_data'] = extracted if extracted else {}
                    if st.session_state['draft_data']:
                        st.toast("Dati estratti per NUOVO format!", icon="✨")
            else:
                st.error("Testo insufficiente nel file.")

# BOX AGGIORNAMENTO DUPLICATI
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

# 2. RICERCA
st.markdown("### 🔎 Ricerca e Selezione")
col_search, col_rst = st.columns([4, 1])
with col_search:
    q = st.text_input("Cerca Format (per contenuto, idea o nome)", placeholder="Es. ponte tibetano, cucina...")
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

if st.session_state['search_results'] is not None:
    options = st.session_state['search_results']
    st.info(f"Filtro AI attivo: {len(options)} format trovati.")
    if st.button("Mostra Tutti"):
        st.session_state['search_results'] = None
        st.rerun()
else:
    options = product_ids

# 3. SELEZIONE E FORM
is_new_mode = False
if st.session_state['draft_data'] and not st.session_state['pending_duplicate']:
    is_new_mode = True
    st.info("✏️ **MODALITÀ CREAZIONE**: Stai modificando i dati estratti dal file caricato.")
    if st.button("🔙 Annulla Creazione"):
        st.session_state['draft_data'] = {}
        st.rerun()
else:
    selected_id = st.selectbox("Seleziona Format da Modificare", options)

st.markdown("### 📝 Dettagli Format")

with st.form("master_form"):
    form_values = {}
    
    if is_new_mode:
        source_data = st.session_state['draft_data']
        current_id_val = str(source_data.get(id_col, ""))
        submit_label = "💾 SALVA NUOVO FORMAT"
    else:
        if selected_id:
            source_data = df.loc[selected_id].to_dict()
            current_id_val = selected_id
            submit_label = "💾 SALVA MODIFICHE"
        else:
            st.warning("Seleziona un format o carica un file.")
            st.form_submit_button("...")
            st.stop()

    # RENDERIZZA ID
    if is_new_mode:
        new_id = st.text_input(f"**{id_col} (UNICO)**", value=current_id_val)
    else:
        st.text_input(f"**{id_col}**", value=current_id_val, disabled=True)
        new_id = current_id_val

    # RENDERIZZA COLONNE CON LOGICA SPECIFICA
    for c in cols:
        val = str(source_data.get(c, ""))
        c_lower = c.lower()
        
        # Pulizia placeholder AI
        if "[[RIEMPIMENTO MANUALE]]" in val: val = ""
        
        # --- LOGICA CAMPI SPECIALI ---
        
        # 1. SOCIAL -> Solo SI/NO
        if "social" in c_lower:
            options_social = ["NO", "SI"]
            # Cerca di capire cosa ha messo l'AI o il DB
            idx_social = 0
            if "si" in val.lower() or "yes" in val.lower(): idx_social = 1
            form_values[c] = st.selectbox(f"**{c}**", options_social, index=idx_social)
            
        # 2. RANKING -> Solo 1-5
        elif "ranking" in c_lower:
            options_ranking = ["1", "2", "3", "4", "5"]
            # Tenta di trovare il numero nel valore attuale
            try:
                curr_rank = str(int(float(val))) if val.strip() else "1"
                if curr_rank not in options_ranking: curr_rank = "3" # Default medio
            except: curr_rank = "3"
            
            form_values[c] = st.selectbox(f"**{c}**", options_ranking, index=options_ranking.index(curr_rank))
            
        # 3. LINK WEBSITE -> Autogenerazione
        elif "link" in c_lower and "website" in c_lower:
            # Se è vuoto o è un nuovo format, rigenera lo slug corretto
            if not val or is_new_mode:
                slug = create_slug(new_id)
                val = f"https://www.teambuilding.it/project/{slug}/"
            form_values[c] = st.text_input(f"**{c}**", value=val)
            
        # 4. DURATA IDEALE (Placeholder hint)
        elif "durata" in c_lower and "ideale" in c_lower:
             form_values[c] = st.text_input(f"**{c}** (Media in ore)", value=val, help="Inserisci un valore medio (es. 3)")

        # 5. ALTRI CAMPI (Testo libero o Area)
        else:
            height = 150 if "descrizione" in c_lower else 0
            if len(val) > 50 or height > 0:
                form_values[c] = st.text_area(f"**{c}**", value=val, height=height if height else None)
            else:
                form_values[c] = st.text_input(f"**{c}**", value=val)

    submitted = st.form_submit_button(submit_label, type="primary")

    if submitted:
        if is_new_mode:
            # SAVE NEW
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
            # UPDATE EXISTING
            updates_count = 0
            try:
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
