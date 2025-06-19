import os
from flask import Flask, abort, jsonify, request, send_file # type: ignore
from flask_cors import CORS # type: ignore
from flask import render_template
import logging
import re
import pandas as pd # type: ignore
from io import BytesIO
import requests
from flasgger import Swagger
from flask_socketio import SocketIO
import threading
import time
import hashlib
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
swagger = Swagger(app)

CORS(app)

# Configura il logger
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Crea un logger
logger = logging.getLogger(__name__)

def clean_text(x):
    if pd.isnull(x):
        return x

    x = str(x).replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    x = re.sub(r'\s+', ' ', x)
    return x.strip().lower()

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

uploaded_file_path = None
intestazione = None
saved_file_name = None

MY_SLEEP_TIME = 10
BACKEND_NAME = "app_read_from_file_and_googlesheet_eventdrv.py"
FRONTEND_NAME = "read_from_file_and_googlesheet_eventdrv.html"
SERVICE_ACCOUNT_FILE = 'spoke9-wp2-55e4997e73fc.json'  # JSON del service account
SPREADSHEET_ID = "1gf7rCnC3fxZUqLKZ40KboGxi2qZ3VGS6awmxTL-69u4"
RANGE_NAME = 'Foglio1!A1:Z1000'

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
)
service = build('sheets', 'v4', credentials=credentials)
sheet = service.spreadsheets()

def get_sheet_data():
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
    return result.get('values', [])

def compute_hash(data):
    flat = ''.join([''.join(row) for row in data])
    return hashlib.md5(flat.encode('utf-8')).hexdigest()

def monitor_sheet():

    logger.info('monitor_sheet: ********** begin.')

    last_hash = None
    while True:
        try:
            logger.info('monitor_sheet: ********** get_sheet_data()')
            data = get_sheet_data()
            logger.info('monitor_sheet: ********** compute_hash()')
            current_hash = compute_hash(data)
            if current_hash != last_hash:
                logger.info('monitor_sheet: ********** current_hash != last_hash')
                socketio.emit('sheet_updated', {'message': 'Il file Google Sheet è stato aggiornato. Ripetere il DOWNLOAD'})
                last_hash = current_hash
            else:
                logger.info('monitor_sheet: ********** current_hash == last_hash')
        except Exception as e:
            logger.error("Errore durante il polling: <%s>", e)

        logger.info('monitor_sheet: ********** time.sleep <%s>', MY_SLEEP_TIME)
        time.sleep(MY_SLEEP_TIME)

@socketio.on('connect')
def handle_connect():
    logger.info('handle_connect: ********** Client connesso')

# Avvia il monitoraggio in un thread separato
threading.Thread(target=monitor_sheet, daemon=True).start()

@app.route('/')
def index():
    return render_template(FRONTEND_NAME)

@app.route('/columns', methods=['GET'])
def get_columns():

    # """
    # Restituisce le intestazioni delle colonne del file Excel caricato.
    # ---
    # parameters:
    #   - name: nome_file
    #     in: query
    #     type: string
    #     required: true
    #     description: Il percorso del file Excel da cui leggere le intestazioni.
    # responses:
    #   200:
    #     description: Lista delle intestazioni
    #     schema:
    #       type: object
    #       properties:
    #         intestazioni:
    #           type: array
    #           items:
    #             type: string
    #   400:
    #     description: Nessun file caricato
    #     schema:
    #       type: object
    #       properties:
    #         error:
    #           type: string
    #           example: Nessun file caricato
    #   500:
    #     description: Errore durante la lettura delle colonne
    #     schema:
    #       type: object
    #       properties:
    #         error:
    #           type: string
    #           example: Errore durante la lettura delle colonne
    # """

    logger.info('get_columns: ********** begin.')

    if uploaded_file_path is None:
        return jsonify({"error": "Nessun file caricato"}), 400

    try:
        # Ottieni il valore del checkbox dalla query string
        intestazione = request.args.get('intestazione')  # '1' se selezionato, None altrimenti
        logger.info('File Excel intestazione: %s', intestazione)

        # Carica il file Excel con o senza intestazione
        if intestazione == '1':
            logger.info('File Excel CON intestazione.')
            df = pd.read_excel(uploaded_file_path, engine='openpyxl')
        else:
            logger.info('File Excel SENZA intestazione.')
            df = pd.read_excel(uploaded_file_path, engine='openpyxl', header=None)

        # Estrai le intestazioni delle colonne
        columns = df.columns.tolist()

        logger.info('get_columns: ********** end.')

        return jsonify(columns)

    except Exception as e:
        logger.error('Errore durante la lettura delle colonne: %s', e)
        return jsonify({"error": "Errore durante la lettura delle colonne"}), 500

@app.route('/upload', methods=['POST'])
def upload_file():

    """
    Carica un file Excel sul server.
    ---
    parameters:
      - in: formData
        name: file
        type: file
        required: true
        description: Il file Excel da caricare
    responses:
      200:
        description: File caricato con successo
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: true
            message:
                type: string
                example: File caricato con successo
            file_path:
                type: string
                example: uploads/nomefile.xlsx
      400:
        description: Errore nella richiesta (file mancante o non selezionato)
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: false
            message:
              type: string
              example: No file part
      500:
        description: Errore durante il caricamento del file
        schema:
          type: object
          properties:
            success:
              type: boolean
              example: false
            message:
              type: string
              example: Errore durante il caricamento del file
    """

    logger.info('upload: ********** begin.')

    global uploaded_file_path
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
    if file:

        try:
            uploaded_file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(uploaded_file_path)
            return jsonify({"success": True, "message": "File uploaded successfully", "file_path": uploaded_file_path})
        except Exception as e:
            logger.error('Errore durante il caricamento del file: %s', e)
            return jsonify({"success": False, "message": "Errore durante il caricamento del file"}), 500

@app.route('/select_sheet', methods=['POST'])
def select_sheet():
    # """
    # Estrae i dati da un Google Sheet e li restituisce come file Excel.
    # ---
    # responses:
    #   200:
    #     description: File Excel generato con successo dai dati del Google Sheet
    #     content:
    #       application/vnd.openxmlformats-officedocument.spreadsheetml.sheet:
    #         schema:
    #           type: string
    #           format: binary
    #   400:
    #     description: Nessun dato trovato nel Google Sheet
    #     schema:
    #       type: string
    #       example: Nessun dato trovato nel Google Sheet.
    #   500:
    #     description: Errore durante la lettura del Google Sheet
    #     schema:
    #       type: string
    #       example: Errore durante la lettura del Google Sheet
    # """
    logger.info('select_sheet: ********** begin.')

    sheet_name = "Foglio1"
    api_key = "AIzaSyDV6ACKDY9Gl19nZmADlOpnUE0JXudPQ0E"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{sheet_name}?key={api_key}"
    response = requests.get(url)
    response.raise_for_status()  # Solleva un errore se la risposta non è 200

    data = response.json()

    values = data.get("values", [])
    if not values:
        logger.warning('select_sheet: ********** Nessun dato trovato nel Google Sheet.')
        return "Nessun dato trovato nel Google Sheet.", 400
    
    df = pd.DataFrame(values[1:], columns=values[0])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    output.seek(0)

    logger.info('select_sheet: ********** end.')

    return send_file(
        output,
        download_name="google_sheet_export.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# Directory privata dove cercare i file
PRIVATE_DIR = '.'

@app.route('/download', methods=['POST'])
def download_file():

    """
    Scarica un file specificato dal server.
    ---
    consumes:
        application/x-www-form-urlencoded
    parameters:
      - in: formData
        name: filename
        type: string
        required: true
        description: Nome del file da scaricare (incluso il percorso relativo se necessario)
    responses:
      200:
        description: File scaricato con successo
        content:
          application/octet-stream:
            schema:
              type: string
              format: binary
      400:
        description: Parametro filename mancante
        schema:
          type: string
          example: Parametro filename mancante.
      404:
        description: File non trovato
        schema:
          type: string
          example: File non trovato.
    """
    logger.info('download: ********** begin.')

    filename = request.form.get('filename')
    if not filename:
        logger.warning('download: filename mancante.')
        abort(400, description="Parametro 'filename' mancante.")

    # Costruisce il percorso completo del file
    file_path = os.path.join(PRIVATE_DIR, filename)

    # Verifica che il file esista
    if not os.path.isfile(file_path):
        logger.warning(f'download: file non trovato: {file_path}')
        abort(404, description="File non trovato.")

    logger.info(f'download: file trovato: {file_path}')
    logger.info('download: ********** end.')

    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/save_results', methods=['GET'])
def save_results():
    
    # """
    # Filtra i dati dal file Excel caricato in base ai parametri forniti e restituisce un file Excel con i risultati.
    # ---
    # parameters:
    #   - name: intestazione
    #     in: query
    #     type: string
    #     required: false
    #     description: 1 se il file ha intestazioni, altrimenti assente
    #   - name: param1
    #     in: query
    #     type: string
    #     required: false
    #     description: Valore da cercare nella colonna 1
    # responses:
    #   200:
    #     description: File Excel con i risultati filtrati
    #     content:
    #       application/vnd.openxmlformats-officedocument.spreadsheetml.sheet:
    #         schema:
    #           type: string
    #           format: binary
    #   400:
    #     description: Nessun file caricato
    #     schema:
    #       type: object
    #       properties:
    #         error:
    #           type: string
    #           example: Nessun file caricato
    #   500:
    #     description: Errore durante il salvataggio
    #     schema:
    #       type: object
    #       properties:
    #         error:
    #           type: string
    #           example: Errore durante il salvataggio
    # """

    logger.info('save_results: ********** begin.')
    try:
        parameters = []
        colonne_dove_cercare = []
        for key, value in request.args.items():
            if key.startswith('param') and value:
                parameters.append(value)
                try:
                    col_index = int(key.replace('param', ''))
                    colonne_dove_cercare.append(col_index)
                except ValueError:
                    pass

        if uploaded_file_path is None:
            return jsonify({"error": "Nessun file caricato"}), 400

        intestazione = request.args.get('intestazione')
        if intestazione == '1':
            df = pd.read_excel(uploaded_file_path, engine='openpyxl')
        else:
            df = pd.read_excel(uploaded_file_path, engine='openpyxl', header=None)

        df = df.applymap(clean_text)
        df = df.where(pd.notnull(df), None)
        parameters = [p.lower() for p in parameters]

        def filter_rows_and(parametri, colonne_dove_cercare):
            def riga_corrisponde(riga):
                return all(any(re.search(r'\b' + re.escape(parametro) + r'\b', str(riga[colonna-1])) for colonna in colonne_dove_cercare) for parametro in parametri)
            return df[df.apply(riga_corrisponde, axis=1)]

        colonne_selezionate = filter_rows_and(parameters, colonne_dove_cercare)

        output = BytesIO()
        colonne_selezionate.to_excel(output, index=False, engine='openpyxl')
        output.seek(0)

        logger.info('save_results: ********** end.')
        return send_file(output, as_attachment=True, download_name="risultati_filtrati.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        logger.error('save_results: errore - %s', e)
        return jsonify({"error": "Errore durante il salvataggio"}), 500

@app.route('/search', methods=['GET'])
def search():

    """
    Esegue una ricerca nel file Excel caricato in base a parametri specificati.
    ---
    parameters:
      - name: intestazione
        in: query
        type: string
        required: false
        description: 1 se il file ha intestazioni, altrimenti assente
      - name: param1
        in: query
        type: string
        required: false
        description: Valore da cercare nella colonna 1
      # Puoi aggiungere altri parametri dinamici se necessario
    responses:
      200:
        description: Lista dei risultati trovati
        schema:
          type: array
          items:
            type: array
            items:
              type: string
      400:
        description: Nessun file caricato o parametri errati
        schema:
          type: object
          properties:
            error:
              type: string
              example: Nessun file caricato
      404:
        description: File non trovato
        schema:
          type: object
          properties:
            read_excel:
              type: string
              example: File non trovato
      500:
        description: Errore inaspettato durante la ricerca
        schema:
          type: object
          properties:
            error:
              type: string
              example: Errore inaspettato
    """
    logger.info('search: ********** begin.')
    try:
        parameters = []
        colonne_dove_cercare = []
        for key, value in request.args.items():
            if key.startswith('param') and value:
                parameters.append(value)
                try:
                    # Estrai il numero dalla chiave, es. 'param3' → 3
                    col_index = int(key.replace('param', ''))
                    colonne_dove_cercare.append(col_index)
                except ValueError:
                    pass # Ignora chiavi non numeriche

        for index, parametro in enumerate(parameters):
            logger.info('search: parametro <%s> (index: %d)', parametro, index)
        
        for index, mycolumn in enumerate(colonne_dove_cercare):
            logger.info('search: mycolumn <%s>', mycolumn)

        # Utilizza il file caricato per la ricerca
        if uploaded_file_path is None:
            logger.error("Nessun file caricato.")
            return jsonify({"error": "Nessun file caricato"}), 400
        
        try:
            intestazione = request.args.get('intestazione') # sarà '1' se selezionato, None se non selezionato
            logger.info('search: File Excel intestazione. %s', intestazione)
            if intestazione == '1':
                logger.info('search: File Excel SI intestazione.')
                df = pd.read_excel(uploaded_file_path, engine='openpyxl')
            else:
                logger.info('search: File Excel NO intestazione.')
                df = pd.read_excel(uploaded_file_path, engine='openpyxl', header=None)

            logger.info('search: File Excel letto con successo.')
            logger.info('search: Contenuto del DataFrame:\n%s', df.head())
        except FileNotFoundError:
            logger.error("search: Il file non è stato trovato.")
            return jsonify({"read_excel": "File non trovato"}), 404
        except Exception as e:
            logger.error('read_excel: errore inaspettato - %s', e)
            return jsonify({"error": "Errore inaspettato"}), 500
        
        logger.info('search: Pulizia dei dati.')
        df = df.applymap(clean_text)
        logger.info('search: Dati puliti:\n%s', df.head())
        
        logger.info('search: df.where')
        df = df.where(pd.notnull(df), None)
        
        logger.info('search: df.iloc')

        logger.info('search: parameters...')
        parameters = [p.lower() for p in parameters]
        logger.info('search: Parametri di ricerca: %s', parameters)
        
        def filter_rows_and(parametri, colonne_dove_cercare):
            for index, colonna in enumerate(colonne_dove_cercare):
                logger.info('search: filter_rows_and: colonna <%s>', colonna)
            
            def riga_corrisponde(riga):
                return all(any(re.search(r'\b' + re.escape(parametro) + r'\b', str(riga[colonna-1])) for colonna in colonne_dove_cercare) for parametro in parametri)

            return df[df.apply(riga_corrisponde, axis=1)]
        
        lista_totale = None
        colonne_selezionate = filter_rows_and(parameters, colonne_dove_cercare)
        if colonne_selezionate is None:
            logger.info("search: Il risultato della funzione è vuoto.")
        else:
            # Procedi con il DataFrame filtrato
            logger.info("search: Il risultato della funzione contiene dati.")
            lista_totale = colonne_selezionate.values.tolist()
            logger.info("search: lista_totale")

        length = len (lista_totale)
        logger.info ('search: lista_totale len: %s', length)
        logger.info ('search: end.**********')
        return jsonify(lista_totale)
    
    except ValueError as ve:
        logger.error('search: errore di valore - %s', ve)
        return jsonify({"error": str(ve)}), 400
    
    except Exception as e:
        logger.error('search: errore inaspettato - %s', e)
        return jsonify({"error": "Errore inaspettato"}), 500

#default porta 5000
if __name__ == '__main__':
    logger.info('Running: <%s>', os.path.basename(__file__))
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)

