import streamlit as st
import gspread
import pandas as pd
import json
import re

# --- Variabile Globale per la Connessione ---
# La variabile che conterrà il nostro oggetto Worksheet (inizializzata più avanti)
ws = None 

# --- Configurazione e Connessione a Google Sheets ---

# @st.cache_resource mantiene la risorsa (la connessione) attiva tra le esecuzioni.
@st.cache_resource
def connect_to_sheet():
    """Stabilisce la connessione con Google Sheets tramite le credenziali."""
    try:
        # Carica le credenziali dai secrets di Streamlit (gcp_service_account)
        credentials_json = st.secrets["gcp_service_account"]
        
        # Connessione
        gc = gspread.service_account_from_dict(credentials_json)
        
        # Apri il foglio di lavoro (Spreadsheet)
        spreadsheet_name = "MasterTbGoogleAi"
        st.caption(f"Tentativo di connessione al file: **{spreadsheet_name}**")
        sh = gc.open(spreadsheet_name)
        
        # Accesso al primo foglio di lavoro (Worksheet)
        worksheet = sh.get_worksheet(0) 
        
        return worksheet
    except Exception as e:
        st.error(f"❌ Errore di connessione a Google Sheets. Dettagli: {e}")
        st.stop()

# --- Inizializzazione della Connessione (Risorsa) ---
# Chiamata all'avvio dell'app per inizializzare la risorsa 'ws'
try:
    ws = connect_to_sheet()
except st.runtime.scriptrunner.StopException:
    st.stop()

# --- Funzioni di Interazione con Google Sheets ---

# @st.cache_data memorizza i dati (il DataFrame) per un accesso rapido.
# Non accetta parametri non hashable.
@st.cache_data(ttl=60) 
def get_all_records():
    """Recupera tutti i record dal foglio di lavoro usando la connessione globale 'ws'."""
    try:
        # Usa la variabile globale 'ws' che è l'oggetto gspread.Worksheet
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
        
        # 1. Trova la riga
        row_index = -1
        for i, row in enumerate(all_values):
            if row and row[0] == product_id:
                row_index = i + 1 
                break
        
        if row_index == -1:
            return False, f"ID Prodotto '{product_id}' non trovato."

        # 2. Trova la colonna
        header = [col.strip() for col in all_values[0]] 
        col_index = -1
        try:
            col_index = header.index(column_name) + 1 
        except ValueError:
             return False, f"Colonna '{column_name}' non trovata nell'header."
        
        # 3. Aggiorna la cella
        ws.update_cell(row_index, col_index, new_value)
        return True, "Aggiornamento riuscito!"
        
    except Exception as e:
        return False, f"Errore durante l'aggiornamento: {e}"

def add_new_row(new_data):
    """Aggiunge una nuova riga al foglio di lavoro."""
    try:
        # Inserisce la riga alla fine del foglio
        ws.insert_row(new_data, index=len(ws.col_values(1)) + 1)
        return True, "Nuovo formato aggiunto con successo!"
    except Exception as e:
        return False, f"Errore nell'aggiunta del formato: {e}"

# --- Interfaccia Utente Streamlit ---

st.title("🤖 Agente Gestore Prodotti Team Building (Google Sheets)")


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
    
    selected_product_id = st.selectbox(
        f"Seleziona il **{product_id_col_name}**:",
        options=product_ids
    )

    if selected_product_id:
        st.subheader(f"Dettagli di: **{selected_product_id}**")
        product_data = df.loc[selected_product_id]
        
        with st.form("edit_form"):
            st.markdown("---")
            new_values = {}
            
            # Creiamo i campi di input per ogni colonna
            for column in column_names:
                current_value = str(product_data[column]) if pd.notna(product_data[column]) else ""
                
                # Usa text_area per campi potenzialmente lunghi
                if len(current_value) > 80 or '\n' in current_value or column not in ['Max Pax', 'Durata Min', 'Durata Max']:
                    new_value = st.text_area(f"**{column}**", value=current_value, key=f"edit_{column}")
                else:
                    new_value = st.text_input(f"**{column}**", value=current_value, key=f"edit_{column}")
                    
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
                    # Forza la ricarica dei dati (svuota la cache) e ricarica l'app
                    get_all_records.clear()
                    st.rerun() # <--- CORREZIONE 1: st.rerun()
            

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
            # Valida che l'ID/Nome Formato sia compilato
            first_col_val = new_row_data[product_id_col_name].strip()
            if not first_col_val:
                st.error(f"Il campo **{product_id_col_name}** è obbligatorio.")
            elif first_col_val in product_ids:
                st.error(f"Il **{product_id_col_name}** '{first_col_val}' esiste già.")
            else:
                # Prepara la lista di valori da inserire (nell'ordine delle colonne)
                values_to_insert = [new_row_data[col] for col in column_names]
                
                success, message = add_new_row(values_to_insert)
                
                if success:
                    st.success(message)
                    st.balloons()
                    # Pulisci la cache e ricarica l'app
                    get_all_records.clear()
                    st.rerun() # <--- CORREZIONE 2: st.rerun()
                else:
                    st.error(message)


# --- TAB 3: Caricamento PDF/PPT (Automazione con AI - Work in Progress) ---
with tab_pdf_ppt:
    st.header("Automazione del Riempimento tramite Documento (PDF/PPT)")
    
    st.info("Siamo pronti per implementare l'integrazione con Gemini (LLM) per estrarre i dati automaticamente dai tuoi documenti e riempire i campi del foglio.")
    
    st.markdown("""
        Per proseguire con l'automazione, dovremo:
        1.  Installare le librerie per la lettura dei documenti (`pypdf`, `python-pptx`).
        2.  Ottenere la tua chiave **Gemini API Key** e configurarla nei Streamlit Secrets.
        3.  Implementare la logica di estrazione AI in questa sezione.
    """)
    
    # Placeholder per l'integrazione AI
    st.subheader("Fase AI: Estrazione Dati")
    # uploaded_file = st.file_uploader("Carica un file PDF o PPT:", type=["pdf", "pptx"])
    # if uploaded_file:
    #     st.warning("Funzionalità in attesa di implementazione.")
