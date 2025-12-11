import streamlit as st
import gspread
import pandas as pd
import json
import re
import ast
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- CONFIGURAZIONE ---
# Modello richiesto dall'utente
SELECTED_AI_MODEL = 'gemini-3-pro-preview'

# --- PROMPT DI SISTEMA ---
FULL_SYSTEM_PROMPT = """
Sei un assistente di ricerca specializzato in format di team building. Analizza la richiesta e il catalogo fornito.
ISTRUZIONI PER L'OUTPUT:
1. DEVI leggere le colonne fornite nel 'CATALOGO PRODOTTI' (Nome Format e Descrizione Breve) per trovare corrispondenze semantiche.
2. Considera solo i valori della colonna 'Nome Format' come output.
3. L'output deve essere SOLO E SOLTANTO una lista Python di stringhe, ad esempio: ['Format A', 'Format B', 'Format C']. 
4. Se non trovi nulla, restituisci una lista vuota: [].
"""

# --- 1. LOGICA DI ACCESSO TRAMITE PASSWORD ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    st.title("Accesso richiesto per 🦁 MasterTb 🦁")
    if "login_password" in st.secrets:
        PASSWORD = st.secrets["login_password"]
        with st.form("login_form"):
            password_input = st.text_input("Inserisci la password", type="password")
            submitted = st.form_submit_button("Accedi")
            if submitted:
                if password_input == PASSWORD:
                    st.session_state['logged_in'] = True
                    st.success("Accesso eseguito con successo!")
                    st.rerun()
                else:
                    st.error("Password errata.")
    else:
        st.error("❌ Errore configurazione: manca 'login_password' nei secrets.")
    st.stop()

# --- VARIABILI GLOBALI E CONNESSIONE ---
ws = None 

@st.cache_resource
def connect_to_sheet():
    try:
        credentials_json = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(credentials_json)
        sh = gc.open("MasterTbGoogleAi")
        worksheet = sh.get_worksheet(0) 
        return worksheet
    except Exception as e:
        st.error(f"❌ Errore connessione Google Sheets: {e}")
        st.stop()

try:
    ws = connect_to_sheet()
except st.runtime.scriptrunner.StopException:
    st.stop()

@st.cache_data(ttl=60) 
def get_all_records():
    try:
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        if not df.empty:
            df.columns = [col.strip() for col in df.columns]
            df.set_index(df.columns[0], inplace=True) 
        return df
    except Exception as e:
        st.error(f"Errore recupero dati: {e}")
        return pd.DataFrame() 

def update_cell(product_id, column_name, new_value):
    try:
        all_values = ws.get_all_values()
        row_index = -1
        for i, row in enumerate(all_values):
            if row and row[0] == product_id:
                row_index = i + 1 
                break
        if row_index == -1: return False, "ID non trovato."
        header = [col.strip() for col in all_values[0]] 
        col_index = header.index(column_name) + 1 
        ws.update_cell(row_index, col_index, new_value)
        return True, "Aggiornamento riuscito!"
    except Exception as e:
        return False, f"Errore: {e}"

def add_new_row(new_data):
    try:
        ws.insert_row(new_data, index=len(ws.col_values(1)) + 1)
        return True, "Formato aggiunto!"
    except Exception as e:
        return False, f"Errore: {e}"

# --- FUNZIONE RICERCA CON GEMINI (Tuo Codice Esatto) ---

@st.cache_data(show_spinner="Ricerca semantica in corso...")
def search_formats_with_gemini(query: str, catalogue_df: pd.DataFrame, product_id_col_name: str) -> list[str]:
    try:
        if "GOOGLE_API_KEY" not in st.secrets:
            return []
            
        # Configurazione Globale
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        
        # Preparazione dati (solo colonne utili)
        all_column_names = list(catalogue_df.columns)
        if len(all_column_names) >= 15: 
            col_16_name = all_column_names[15] 
            sub_df = catalogue_df[[col_16_name]] 
            catalogue_string = sub_df.to_markdown(index=True)
        else:
            catalogue_string = catalogue_df.to_markdown(index=True)

        # --- IL TUO CODICE ESATTO ---
        model = genai.GenerativeModel(
          model_name=SELECTED_AI_MODEL, 
          generation_config={"temperature": 0.0},
          system_instruction=FULL_SYSTEM_PROMPT,
          safety_settings={
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
          },
        )
        # -----------------------------

        # Prompt Combinato
        final_prompt = f"CATALOGO PRODOTTI:\n{catalogue_string}\n\nQUERY UTENTE: {query}"
        
        response = model.generate_content(final_prompt)
        raw_text = response.text.strip()
        
        # Parsing
        match = re.search(r"(\[.*\])", raw_text, re.DOTALL)
        list_string = match.group(1) if match else raw_text
        
        result_list = ast.literal_eval(list_string)
        if isinstance(result_list, list):
            return result_list
        return []

    except Exception as e:
        st.error(f"Errore Gemini: {e}")
        return []

# --- INTERFACCIA UTENTE ---

st.title("🦁 MasterTb 🦁")
df = get_all_records()

if df.empty:
    st.stop()

product_ids = [str(id) for id in df.index.tolist()]
column_names = df.columns.tolist()
product_id_col_name = df.index.name

tab_view_edit, tab_add_format, tab_pdf_ppt = st.tabs(["👁️ Visualizza & Modifica", "➕ Aggiungi Nuovo", "📄 Caricamento Doc."])

with tab_view_edit:
    st.header("Visualizza e Modifica")
    st.info(f"Modello AI: **{SELECTED_AI_MODEL}**")
    
    search_query = st.text_input(f"Cerca {product_id_col_name} con AI:", placeholder="es. format dove si vola")
    filtered_ids = []
    
    if search_query:
        if "GOOGLE_API_KEY" in st.secrets:
            gemini_results = search_formats_with_gemini(search_query, df, product_id_col_name)
            if gemini_results:
                filtered_ids = [id for id in gemini_results if id in product_ids]
                if not filtered_ids: st.warning("Format trovati ma non corrispondenti al catalogo.")
            else:
                filtered_ids = [id for id in product_ids if search_query.lower() in id.lower()]
                if filtered_ids: st.info("Uso ricerca testuale (fallback).")
        else:
            filtered_ids = [id for id in product_ids if search_query.lower() in id.lower()]

    if not search_query: filtered_ids = product_ids
    
    if not filtered_ids:
        st.error("Nessun formato trovato.")
    else:
        selected_product_id = st.selectbox(f"Seleziona {product_id_col_name}:", options=filtered_ids)
        
        if selected_product_id:
            product_data = df.loc[selected_product_id]
            with st.form("edit_form"):
                new_values = {}
                for column in column_names:
                    val = str(product_data[column]) if pd.notna(product_data[column]) else ""
                    key = f"edit_{selected_product_id}_{column}"
                    if len(val) > 80 or '\n' in val:
                        new_values[column] = st.text_area(f"**{column}**", value=val, key=key)
                    else:
                        new_values[column] = st.text_input(f"**{column}**", value=val, key=key)
                
                if st.form_submit_button("Salva Modifiche 💾"):
                    changes = False
                    for col, new_val in new_values.items():
                        old_val = str(product_data[col]) if pd.notna(product_data[col]) else ""
                        if old_val.strip() != str(new_val).strip():
                            success, msg = update_cell(selected_product_id, col, new_val)
                            if success: 
                                st.success(f"Aggiornato {col}")
                                changes = True
                            else: st.error(msg)
                    if changes:
                        get_all_records.clear()
                        st.rerun()
                    else: st.warning("Nessuna modifica.")

with tab_add_format:
    st.header("Aggiungi Nuovo")
    with st.form("add_form"):
        new_row = {}
        for col in column_names:
            if col == product_id_col_name:
                new_row[col] = st.text_input(f"**{col} (UNICO)** *", key=f"add_{col}")
            else:
                new_row[col] = st.text_area(f"**{col}**", key=f"add_{col}")
        
        if st.form_submit_button("Aggiungi 🚀"):
            if not new_row[product_id_col_name].strip():
                st.error("Nome mancante.")
            elif new_row[product_id_col_name] in product_ids:
                st.error("Esiste già.")
            else:
                if add_new_row([new_row[c] for c in column_names]):
                    st.success("Aggiunto!")
                    get_all_records.clear()
                    st.rerun()

with tab_pdf_ppt:
    st.header("Automazione Documenti")
    st.info("Pronto per l'integrazione PDF/PPT con librerie installate.")
