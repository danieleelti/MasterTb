import streamlit as st
import gspread
import pandas as pd
import json
from io import StringIO
import re # Aggiunto per pulizia stringhe

# --- Configurazione e Connessione a Google Sheets ---

# Usiamo st.cache_resource per mantenere la connessione aperta tra le esecuzioni.
@st.cache_resource
def connect_to_sheet():
    """Stabilisce la connessione con Google Sheets tramite le credenziali."""
    try:
        # Carica le credenziali dai secrets di Streamlit
        credentials_json = st.secrets["gcp_service_account"]
        
        # Connessione
        gc = gspread.service_account_from_dict(credentials_json)
        
        # Apri il foglio di lavoro
        spreadsheet_name = "MasterTbGoogleAi"
        st.caption(f"Tentativo di connessione al foglio: **{spreadsheet_name}**")
        sh = gc.open(spreadsheet_name)
        
        # Supponiamo che la tabella sia nel primo foglio (Worksheet)
        # *** Importante: verifica il nome esatto del tuo foglio se non è 'Foglio1' ***
        worksheet = sh.worksheet("Foglio1") 
        
        return worksheet
    except Exception as e:
        st.error(f"Errore di connessione a Google Sheets. Verifica le credenziali e che il foglio 'MasterTbGoogleAi' esista e sia condiviso con l'email del Service Account. Dettagli: {e}")
        st.stop()

# Connessione al foglio di lavoro
ws = connect_to_sheet()

# --- Funzioni di Interazione con Google Sheets ---

@st.cache_data(ttl=60) # Caching per non ricaricare i dati ad ogni interazione
def get_all_records(worksheet):
    """Recupera tutti i record dal foglio di lavoro."""
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    # Imposta la prima colonna (che è "Nome Format") come indice
    if not df.empty:
        # Pulisce i nomi delle colonne da spazi bianchi e caratteri non voluti per maggiore sicurezza
        df.columns = [col.strip() for col in df.columns]
        # La prima colonna è l'ID/Nome Prodotto
        df.set_index(df.columns[0], inplace=True) 
    return df

def update_cell(worksheet, product_id, column_name, new_value, df_columns):
    """Aggiorna una singola cella nel foglio di lavoro."""
    try:
        # Prendiamo tutti i valori per trovare l'indice esatto
        all_values = worksheet.get_all_values()
        
        # 1. Troviamo la riga
        row_index = -1
        # Assumiamo che l'ID sia nella prima colonna
        product_id_col_name = all_values[0][0] # Nome della prima colonna (e quindi l'ID)
        
        for i, row in enumerate(all_values):
            # i+1 è l'indice 1-based in gspread, usiamo la prima colonna per il match
            if row and row[0] == product_id:
                row_index = i + 1 
                break
        
        if row_index == -1:
            return False, f"ID Prodotto '{product_id}' non trovato nel foglio."

        # 2. Trova la colonna
        header = [col.strip() for col in all_values[0]] # Pulisci l'header
        col_index = -1
        try:
            # +1 perché gspread è 1-based
            col_index = header.index(column_name) + 1 
        except ValueError:
             return False, f"Colonna '{column_name}' non trovata nell'header."
        
        # 3. Aggiorna la cella
        worksheet.update_cell(row_index, col_index, new_value)
        return True, "Aggiornamento riuscito!"
        
    except Exception as e:
        return False, f"Errore durante l'aggiornamento: {e}"

def add_new_row(worksheet, new_data, product_id_col_name):
    """Aggiunge una nuova riga al foglio di lavoro."""
    try:
        # Inserisce la riga alla fine
        # len(worksheet.col_values(1)) è il numero di righe attuali (compreso l'header)
        worksheet.insert_row(new_data, index=len(worksheet.col_values(1)) + 1)
        return True, "Nuovo formato aggiunto con successo!"
    except Exception as e:
        return False, f"Errore nell'aggiunta del formato: {e}"

# --- Interfaccia Utente Streamlit ---

st.title("🤖 Agente Gestore Prodotti Team Building (Google Sheets)")

# Carica i dati una volta
df = get_all_records(ws)

if df.empty:
    st.warning("Il foglio di lavoro è vuoto o non è stato possibile caricare i dati (potrebbe mancare l'header). Assicurati che il tuo foglio contenga le colonne specificate.")
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
        
        # Mostra i dati in una lista per la visualizzazione e modifica
        with st.form("edit_form"):
            st.markdown("---")
            new_values = {}
            
            # Creiamo i campi di input per ogni colonna
            for column in column_names:
                current_value = str(product_data[column]) if pd.notna(product_data[column]) else ""
                
                # Usiamo un text_area se il contenuto è lungo, altrimenti un text_input
                if len(current_value) > 50 or '\n' in current_value:
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
                        # L'indice della colonna nel dataframe è 0-based, ma la funzione update_cell
                        # ha bisogno del nome della colonna
                        success, message = update_cell(ws, selected_product_id, column, new_val, column_names)
                        
                        if success:
                            st.success(f"✔️ Aggiornato **{column}** a: *{new_val[:50]}...*")
                            changes_made = True
                        else:
                            st.error(f"❌ Errore aggiornamento {column}: {message}")
                
                if not changes_made:
                    st.warning("Nessuna modifica rilevata. Niente da salvare.")
                else:
                    st.balloons()
                    # Forza la ricarica dei dati per aggiornare la cache
                    get_all_records.clear()
                    st.experimental_rerun() # Ricarica l'app per mostrare i dati aggiornati
            

# --- TAB 2: Aggiungi Nuovo Formato ---
with tab_add_format:
    st.header("Aggiungi un Nuovo Formato (Riga)")
    
    st.info(f"Saranno elencati tutti i campi. Il primo campo, **{product_id_col_name}**, deve essere unico e compilato.")
    
    with st.form("add_form"):
        new_row_data = {}
        for column in column_names:
            # Il primo campo (l'ID: "Nome Format") è cruciale e deve essere univoco
            if column == product_id_col_name:
                new_row_data[column] = st.text_input(f"**{column} (ID/Nome Formato UNICO)** *", key=f"add_{column}")
            else:
                # Usa text_area per tutti gli altri campi per permettere più testo
                new_row_data[column] = st.text_area(f"**{column}**", key=f"add_{column}")
        
        submitted_add = st.form_submit_button("Aggiungi Riga/Formato 🚀")
        
        if submitted_add:
            # Valida che l'ID/Nome Formato sia compilato
            first_col_val = new_row_data[product_id_col_name].strip()
            if not first_col_val:
                st.error(f"Il campo **{product_id_col_name}** è obbligatorio.")
            elif first_col_val in product_ids:
                st.error(f"Il **{product_id_col_name}** '{first_col_val}' esiste già. Scegline uno unico.")
            else:
                # Prepara la lista di valori da inserire (nell'ordine delle colonne)
                values_to_insert = [new_row_data[col] for col in column_names]
                
                success, message = add_new_row(ws, values_to_insert, product_id_col_name)
                
                if success:
                    st.success(message)
                    st.balloons()
                    # Pulisci la cache e ricarica
                    get_all_records.clear()
                    st.experimental_rerun()
                else:
                    st.error(message)


# --- TAB 3: Caricamento PDF/PPT (Automazione con AI - Work in Progress) ---
with tab_pdf_ppt:
    st.header("Automazione del Riempimento tramite Documento (PDF/PPT)")
    
    st.warning("⚠️ Questa è la fase 3 e richiede l'integrazione con un modello di AI (LLM) per l'estrazione delle informazioni. Procediamo ora con l'analisi e l'estrazione automatizzata.")
    
    st.markdown(f"""
        L'obiettivo è estrarre in automatico i seguenti campi dal tuo documento:
        
        - **{product_id_col_name}**
        - **Tipologia**
        - **Logistica**
        - ... e tutti gli altri **{len(column_names)}** campi.
    """)
    
    # Placeholder per il caricamento
    # uploaded_file = st.file_uploader("Carica un file PDF o PPT per l'analisi:", type=["pdf", "pptx"])
    # if uploaded_file:
    #     st.info("Pronto per l'analisi AI...")
