import streamlit as st
import gspread
import pandas as pd
import json
import re
from google import genai
from google.genai import types 
import ast
from google.genai.types import HarmCategory, HarmBlockThreshold 

# --- IMPOSTAZIONE MODELLO FISSO DI RIFERIMENTO ---
# Modello fisso come richiesto dall'utente.
SELECTED_AI_MODEL = 'gemini-3-pro-preview'

# --- PROMPT DI SISTEMA FISSO ---
# Istruzioni fisse che definiscono il ruolo del modello e l'output richiesto.
FULL_SYSTEM_PROMPT = """
Sei un assistente di ricerca specializzato in format di team building. Analizza la richiesta e il catalogo fornito.
ISTRUZIONI PER L'OUTPUT:
1. DEVI leggere TUTTE le colonne del 'CATALOGO PRODOTTI' (Descrizione Breve, Tipologia, Vibe / Emozione, ecc.) per trovare corrispondenze semantiche.
2. Considera solo i valori della colonna 'Nome Format' come output.
3. L'output deve essere SOLO E SOLTANTO una lista Python di stringhe, ad esempio: ['Format A', 'Format B', 'Format C']. 
4. Se non trovi nulla, restituisci una lista vuota: [].
"""

# --- 1. LOGICA DI ACCESSO TRAMITE PASSWORD ---

# Inizializza lo stato di sessione
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

# Se l'utente non è loggato, mostra il form di login e ferma l'esecuzione
if not st.session_state['logged_in']:
    st.title("Accesso richiesto per 🦁 MasterTb 🦁")
    
    # Verifica che il secret della password esista
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
        st.error("❌ Errore di configurazione: la chiave 'login_password' non è stata trovata nei secrets di Streamlit.")
    
    st.stop() # Ferma l'esecuzione di tutto il codice sottostante finché l'accesso non è eseguito

# --- IL RESTO DEL CODICE CONTINUA SOLO SE st.session_state['logged_in'] è True ---

# --- Variabile Globale per la Connessione ---
ws = None 

# --- Configurazione e Connessione a Google Sheets ---

@st.cache_resource
def connect_to_sheet():
    """Stabilisce la connessione con Google Sheets tramite le credenziali."""
    try:
        credentials_json = st.secrets["gcp_service_account"]
        gc = gspread.service_account_from_dict(credentials_json)
        spreadsheet_name = "MasterTbGoogleAi"
        st.caption(f"Tentativo di connessione al file: **{spreadsheet_name}**")
        sh = gc.open(spreadsheet_name)
        worksheet = sh.get_worksheet(0) 
        return worksheet
    except Exception as e:
        st.error(f"❌ Errore di connessione a Google Sheets. Dettagli: {e}")
        st.stop()

# --- Inizializzazione della Connessione (Risorsa) ---
try:
    ws = connect_to_sheet()
except st.runtime.scriptrunner.StopException:
    st.stop()

# --- Funzioni di Interazione con Google Sheets ---

@st.cache_data(ttl=60) 
def get_all_records():
    """Recupera tutti i record dal foglio di lavoro usando la connessione globale 'ws'."""
    try:
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        if not df.empty:
            df.columns = [col.strip() for col in df.columns]
            df.set_index(df.columns[0], inplace=True) 
            
        return df
    except Exception as e:
        st.error(f"Errore nel recupero dei dati: {e}. Controlla che la prima riga contenga i tuoi header.")
        return pd.DataFrame() 

def update_cell(product_id, column_name, new_value):
    """Aggiorna una singola cella nel foglio di lavoro."""
    try:
        all_values = ws.get_all_values()
        row_index = -1
        for i, row in enumerate(all_values):
            if row and row[0] == product_id:
                row_index = i + 1 
                break
        
        if row_index == -1:
            return False, f"ID Prodotto '{product_id}' non trovato."

        header = [col.strip() for col in all_values[0]] 
        col_index = -1
        try:
            col_index = header.index(column_name) + 1 
        except ValueError:
             return False, f"Colonna '{column_name}' non trovata nell'header."
        
        ws.update_cell(row_index, col_index, new_value)
        return True, "Aggiornamento riuscito!"
        
    except Exception as e:
        return False, f"Errore durante l'aggiornamento: {e}"

def add_new_row(new_data):
    """Aggiunge una nuova riga al foglio di lavoro."""
    try:
        ws.insert_row(new_data, index=len(ws.col_values(1)) + 1)
        return True, "Nuovo formato aggiunto con successo!"
    except Exception as e:
        return False, f"Errore nell'aggiunta del formato: {e}"

# --- FUNZIONE DI RICERCA SEMANTICA CON GEMINI ---

@st.cache_data(show_spinner="Ricerca semantica in corso...")
def search_formats_with_gemini(query: str, catalogue_df: pd.DataFrame, product_id_col_name: str) -> list[str]:
    """
    Usa Gemini per eseguire una ricerca semantica basata sulla query, usando la sintassi API universale.
    Restituisce una lista di nomi di formati pertinenti.
    """
    
    try:
        if "GOOGLE_API_KEY" not in st.secrets:
            return [] 
            
        # Ritorno alla sintassi universale client.models.generate_content
        client = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
        catalogue_string = catalogue_df.to_markdown(index=True)

        # Costruisco il prompt completo includendo le istruzioni di sistema direttamente
        full_prompt_text = (
            FULL_SYSTEM_PROMPT + 
            f"\n\nCATALOGO PRODOTTI:\n{catalogue_string}" +
            f"\n\nQUERY UTENTE: {query}"
        )

        # Configurazione Safety Settings (richiede types.GenerateContentConfig)
        config = types.GenerateContentConfig(
            temperature=0.0,
            system_instruction=FULL_SYSTEM_PROMPT,
            safety_settings=[
                types.SafetySetting(
                    category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=HarmBlockThreshold.BLOCK_NONE,
                ),
            ]
        )
        
        # Chiamata API compatibile con tutte le versioni di google-genai
        response = client.models.generate_content(
            model=SELECTED_AI_MODEL, 
            contents=full_prompt_text,
            config=config # Passiamo la configurazione, inclusi i safety settings
        )
        
        raw_text = response.text.strip()
        
        # LOGICA DI PARSING ROBUSTA
        match = re.search(r"(\[.*\])", raw_text, re.DOTALL)
        
        if match:
            list_string = match.group(1)
        else:
            list_string = raw_text 
            
        try:
            result_list = ast.literal_eval(list_string)
            if isinstance(result_list, list) and all(isinstance(item, str) for item in result_list):
                return result_list
            else:
                st.warning(f"❌ L'AI ha restituito un formato non valido. Output grezzo AI: `{raw_text}`")
                return []
        except:
            st.warning(f"❌ Errore di parsing del risultato AI. L'AI ha restituito: `{raw_text}`")
            return []

    except Exception as e:
        # In caso di errore API, ritorna una lista vuota
        st.error(f"Errore durante la chiamata a Gemini: {e}")
        return []


# --- Interfaccia Utente Streamlit (Inizia solo dopo il Login) ---

st.title("🦁 MasterTb 🦁")

# Carica i dati
df = get_all_records()

if df.empty:
    st.warning("Il foglio di lavoro è vuoto o non è stato possibile caricare i dati.")
    st.stop()

product_ids = [str(id) for id in df.index.tolist()]
column_names = df.columns.tolist()
product_id_col_name = df.index.name # Recupera "Nome Format"

# TABS: Per separare le funzionalità
tab_view_edit, tab_add_format, tab_pdf_ppt = st.tabs([
    "👁️ Visualizza & Modifica", 
    "➕ Aggiungi Nuovo Formato", 
    "📄 Caricamento Doc. (WIP)"
])

# --- TAB 1: Visualizza e Modifica ---
with tab_view_edit:
    st.header("Visualizza e Modifica un Formato Esistente")
    
    # --- INFORMAZIONE SUL MODELLO FISSO (NIENTE SELETTORE) ---
    st.info(f"Modello AI in uso (Fisso): **{SELECTED_AI_MODEL}** (Modalità di chiamata API universale)")
    st.markdown("---")
    # --- FINE INFORMAZIONE MODELLO ---

    search_query = st.text_input(
        f"Cerca il **{product_id_col_name}** con l'AI:", 
        placeholder="Esempio: 'il format dove si vola' o 'prodotti a basso costo'",
        key="search_format_input"
    )

    filtered_ids = []
    
    if search_query:
        if "GOOGLE_API_KEY" in st.secrets:
            # Chiamata alla funzione Gemini (Ricerca Semantica)
            gemini_results = search_formats_with_gemini(search_query, df, product_id_col_name)
        else:
            gemini_results = []
            st.info("Ricerca testuale classica in uso (chiave Google API non trovata).")
            
        
        if gemini_results:
            # Filtra solo gli ID che esistono effettivamente nel catalogo (per sicurezza)
            filtered_ids = [id for id in gemini_results if id in product_ids]
            
            if not filtered_ids and gemini_results:
                 st.warning(f"L'AI ha identificato i seguenti format: {', '.join(gemini_results)}, ma non sono stati trovati nel catalogo (problema di corrispondenza esatta dei nomi).")
        
        if not gemini_results:
            # Fallback alla ricerca testuale standard se l'AI non trova nulla
            filtered_ids = [id for id in product_ids if search_query.lower() in id.lower()]
            
            if filtered_ids:
                 st.info("Ricerca semantica non riuscita, mostra risultati per corrispondenza testuale.")

            
    # Se la ricerca è vuota, usiamo l'elenco completo per la selezione iniziale
    if not search_query:
        filtered_ids = product_ids
        
    # --- Mostra e Seleziona dai risultati ---
    if not filtered_ids:
        st.error("Nessun formato trovato.")
        selected_product_id = None
    else:
        # 3. Selectbox con i risultati filtrati
        selected_product_id = st.selectbox(
            f"Seleziona il **{product_id_col_name}** (risultati filtrati):",
            options=filtered_ids,
            index=0 if filtered_ids else None
        )

    # --- Mostra e Modifica i dettagli ---
    if selected_product_id:
        st.subheader(f"Dettagli di: **{selected_product_id}**")
        product_data = df.loc[selected_product_id]
        
        with st.form("edit_form"):
            st.markdown("---")
            new_values = {}
            
            # Creiamo i campi di input per ogni colonna
            for column in column_names:
                current_value = str(product_data[column]) if pd.notna(product_data[column]) else ""
                
                # Rende la chiave dinamica includendo l'ID
                dynamic_key = f"edit_{selected_product_id}_{column}"
                
                # Usa text_area per campi potenzialmente lunghi
                if len(current_value) > 80 or '\n' in current_value or column not in ['Max Pax', 'Durata Min', 'Durata Max']:
                    new_value = st.text_area(f"**{column}**", 
                                             value=current_value, 
                                             key=dynamic_key)
                else:
                    new_value = st.text_input(f"**{column}**", 
                                              value=current_value, 
                                              key=dynamic_key)
                    
                new_values[column] = new_value

            submitted = st.form_submit_button("Salva Modifiche 💾")
            
            if submitted:
                changes_made = False
                st.info("Inizio l'aggiornamento...")
                
                # Itera sui valori e aggiorna solo quelli che sono cambiati
                for column, new_val in new_values.items():
                    old_val = str(product_data[column]) if pd.notna(product_data[column]) else ""
                    
                    if str(old_val).strip() != str(new_val).strip():
                        success, message = update_cell(selected_product_id, column, new_val)
                        
                        if success:
                            st.success(f"✔️ Aggiornato **{column}**")
                            changes_made = True
                        else:
                            st.error(f"❌ Errore aggiornamento {column}: {message}")
                
                if not changes_made:
                    st.warning("Nessuna modifica rilevata. Niente da salvare.")
                else:
                    st.balloons()
                    get_all_records.clear()
                    st.rerun() 
            

# --- TAB 2: Aggiungi Nuovo Formato ---
with tab_add_format:
    st.header("Aggiungi un Nuovo Formato (Riga)")
    
    st.info(f"Devi riempire tutti i campi. Il campo **{product_id_col_name}** deve essere unico.")
    
    with st.form("add_form"):
        new_row_data = {}
        for column in column_names:
            if column == product_id_col_name:
                new_row_data[column] = st.text_input(f"**{column} (ID/Nome Formato UNICO)** *", key=f"add_{column}")
            else:
                new_row_data[column] = st.text_area(f"**{column}**", key=f"add_{column}")
        
        submitted_add = st.form_submit_button("Aggiungi Riga/Formato 🚀")
        
        if submitted_add:
            first_col_val = new_row_data[product_id_col_name].strip()
            if not first_col_val:
                st.error(f"Il campo **{product_id_col_name}** è obbligatorio.")
            elif first_col_val in product_ids:
                st.error(f"Il **{product_id_col_name}** '{first_col_val}' esiste già.")
            else:
                values_to_insert = [new_row_data[col] for col in column_names]
                
                success, message = add_new_row(values_to_insert)
                
                if success:
                    st.success(message)
                    st.balloons()
                    get_all_records.clear()
                    st.rerun() 
                else:
                    st.error(message)


# --- TAB 3: Caricamento PDF/PPT (Automazione con AI - Work in Progress) ---
with tab_pdf_ppt:
    st.header("Automazione del Riempimento tramite Documento (PDF/PPT)")
    
    # Mostra quale modello è in uso
    st.info(f"Il modello selezionato per l'analisi è: **{SELECTED_AI_MODEL}**. Dovremo installare le librerie necessarie per leggere i file.")
    
    st.markdown("""
        Per proseguire con l'automazione, dovremo:
        1.  **Installare le librerie** per la lettura dei documenti (`pypdf`, `python-pptx`).
        2.  Usare la chiave **Google API Key** già configurata.
        3.  Implementare la logica di estrazione AI in questa sezione.
    """)
    
    # Placeholder per l'integrazione AI
    st.subheader("Fase AI: Estrazione Dati")
    # uploaded_file = st.file_uploader("Carica un file PDF o PPT:", type=["pdf", "pptx"])
    # if uploaded_file:
    #     st.warning("Funzionalità in attesa di implementazione.")
