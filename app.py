import streamlit as st
import gspread
import pandas as pd
import json
from io import StringIO

# --- Configurazione e Connessione a Google Sheets ---

# Usiamo st.cache_resource per mantenere la connessione aperta tra le esecuzioni.
@st.cache_resource
def connect_to_sheet():
    """Stabilisce la connessione con Google Sheets tramite le credenziali."""
    try:
        # Carica le credenziali dai secrets di Streamlit
        # Assicurati di configurare i tuoi secrets di Streamlit con la chiave "gcp_service_account"
        # che contiene l'intero JSON delle credenziali.
        credentials_json = st.secrets["gcp_service_account"]
        
        # Connessione
        gc = gspread.service_account_from_dict(credentials_json)
        
        # Apri il foglio di lavoro
        spreadsheet_name = "MasterTbGoogleAi"
        sh = gc.open(spreadsheet_name)
        
        # Supponiamo che la tabella sia nel primo foglio (Worksheet)
        worksheet = sh.worksheet("Foglio1") # Aggiorna "Foglio1" con il nome esatto del tuo foglio se diverso
        
        return worksheet
    except Exception as e:
        st.error(f"Errore di connessione a Google Sheets: {e}")
        st.stop()

# Connessione al foglio di lavoro
ws = connect_to_sheet()

# --- Funzioni di Interazione con Google Sheets ---

@st.cache_data(ttl=60) # Caching per non ricaricare i dati ad ogni interazione
def get_all_records(worksheet):
    """Recupera tutti i record dal foglio di lavoro."""
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    # Assumiamo che la prima colonna sia l'ID o il nome univoco del prodotto
    if not df.empty:
        df.set_index(df.columns[0], inplace=True) 
    return df

def update_cell(worksheet, product_id, column_name, new_value, df_columns):
    """Aggiorna una singola cella nel foglio di lavoro."""
    try:
        # Trova l'indice della riga (contando da 1, e +1 per l'header)
        # Questo richiede di sapere dove si trova l'ID nel foglio
        
        # Prendiamo tutti i valori per trovare l'indice esatto
        all_values = worksheet.get_all_values()
        
        # Troviamo la riga
        row_index = -1
        for i, row in enumerate(all_values):
            # Assumiamo che l'ID sia nella prima colonna (indice 0)
            if row and row[0] == product_id:
                # L'indice della riga in gspread è 1-based, e l'header è la riga 1
                row_index = i + 1 
                break
        
        if row_index == -1:
            return False, f"ID Prodotto '{product_id}' non trovato."

        # Trova l'indice della colonna (contando da 1)
        # Troviamo l'indice del nome della colonna nell'header (all_values[0])
        col_index = -1
        header = all_values[0]
        try:
            col_index = header.index(column_name) + 1 # +1 perché gspread è 1-based
        except ValueError:
             return False, f"Colonna '{column_name}' non trovata."
        
        # Aggiorna la cella
        worksheet.update_cell(row_index, col_index, new_value)
        return True, "Aggiornamento riuscito!"
        
    except Exception as e:
        return False, f"Errore durante l'aggiornamento: {e}"

def add_new_row(worksheet, new_data):
    """Aggiunge una nuova riga al foglio di lavoro."""
    try:
        # gspread.insert_row prende una lista di valori
        worksheet.insert_row(new_data, index=len(worksheet.col_values(1)) + 1)
        return True, "Nuovo formato aggiunto con successo!"
    except Exception as e:
        return False, f"Errore nell'aggiunta del formato: {e}"

# --- Interfaccia Utente Streamlit ---

st.title("🤖 Agente Gestore Prodotti Team Building (Google Sheets)")
st.caption(f"Connesso al foglio: **MasterTbGoogleAi**")

# Carica i dati una volta
df = get_all_records(ws)
product_ids = [str(id) for id in df.index.tolist()]
column_names = df.columns.tolist()

# TABS: Per separare le funzionalità
tab_view_edit, tab_add_format, tab_pdf_ppt = st.tabs([
    "👁️ Visualizza & Modifica", 
    "➕ Aggiungi Nuovo Formato", 
    "📄 Caricamento Doc. (WIP)"
])

# --- TAB 1: Visualizza e Modifica ---
with tab_view_edit:
    st.header("Visualizza e Modifica un Formato Esistente")
    
    if not df.empty:
        selected_product_id = st.selectbox(
            "Seleziona l'ID/Nome del Prodotto da Visualizzare/Modificare:",
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
            
    else:
        st.warning("Il foglio di lavoro è vuoto o non è stato possibile caricare i dati.")

# --- TAB 2: Aggiungi Nuovo Formato ---
with tab_add_format:
    st.header("Aggiungi un Nuovo Formato (Riga)")
    
    if not column_names:
        st.warning("Impossibile determinare le intestazioni delle colonne. Controlla il foglio.")
    else:
        st.info("Saranno elencati tutti i campi (colonne) da riempire.")
        
        with st.form("add_form"):
            new_row_data = {}
            for column in column_names:
                # Il primo campo (l'ID) è cruciale e deve essere univoco
                if column == column_names[0]:
                    new_row_data[column] = st.text_input(f"**{column} (ID/Nome Formato UNICO)** *", key=f"add_{column}")
                else:
                    new_row_data[column] = st.text_area(f"**{column}**", key=f"add_{column}")
            
            submitted_add = st.form_submit_button("Aggiungi Riga/Formato 🚀")
            
            if submitted_add:
                # Valida che l'ID/Nome Formato sia compilato
                first_col_val = new_row_data[column_names[0]].strip()
                if not first_col_val:
                    st.error(f"Il campo **{column_names[0]}** è obbligatorio.")
                elif first_col_val in product_ids:
                    st.error(f"L'ID/Nome Formato **{first_col_val}** esiste già. Scegline uno unico.")
                else:
                    # Prepara la lista di valori da inserire
                    values_to_insert = [new_row_data[col] for col in column_names]
                    
                    success, message = add_new_row(ws, values_to_insert)
                    
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
    st.markdown("""
        Questa sezione è il **prossimo passo complesso** che richiede l'integrazione di un modello AI (LLM) 
        per l'estrazione delle informazioni.
        
        **Il flusso sarebbe:**
        
        1.  **Caricamento Documento:** L'utente carica il PDF/PPT.
        2.  **Estrazione Testo:** Il codice estrae il testo dal documento (usando librerie come `pypdf` o `python-pptx`).
        3.  **Prompting AI:** Il testo estratto viene inviato a un modello AI (es. Gemini) con un prompt strutturato 
            che chiede di estrarre e formattare le informazioni esattamente nei campi del foglio Google (es. "Nome Formato", "Descrizione", "Obiettivo", ecc.).
        4.  **Aggiunta Riga:** Il risultato dell'AI, formattato come una riga, viene aggiunto al foglio.
    """)
    
    st.info("Per implementare questo, avrai bisogno di un'API di Google AI (come Gemini API) e delle librerie per la lettura dei documenti (`pypdf`, `python-pptx`).")
    
    # Placeholder per il caricamento
    # uploaded_file = st.file_uploader("Carica un file PDF o PPT per l'analisi:", type=["pdf", "pptx"])
    # if uploaded_file:
    #     st.warning("Funzionalità non ancora implementata. Aggiungeremo l'integrazione AI qui!")

---

## 💡 Istruzioni Aggiuntive per GitHub + Streamlit

Per far funzionare questo codice su Streamlit Cloud (che è la via più semplice per deployare Streamlit da GitHub), devi configurare i `Secrets`:

1.  Nel tuo repository GitHub, crea il file `requirements.txt` con:
    ```
    streamlit
    gspread
    pandas
    ```
2.  Quando fai il deploy su Streamlit Cloud (o se usi un server locale con le variabili d'ambiente), devi creare la sezione **Secrets**.

### Come configurare i Secrets di Streamlit

1.  Apri il file JSON delle credenziali del tuo Service Account.
2.  Copia l'intero contenuto del file JSON.
3.  Nel tuo progetto Streamlit Cloud, vai su **Settings > Secrets**.
4.  Aggiungi una nuova secret con il nome `gcp_service_account` e incolla l'intero JSON come valore.

Il formato nel file `secrets.toml` di Streamlit (o la stringa di testo nel campo Secret) sarà simile a questo:

```toml
# .streamlit/secrets.toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@project-id.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "[https://accounts.google.com/o/oauth2/auth](https://accounts.google.com/o/oauth2/auth)"
token_uri = "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)"
auth_provider_x509_cert_url = "..."
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
