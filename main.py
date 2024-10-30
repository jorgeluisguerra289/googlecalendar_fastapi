

from fastapi import FastAPI, Request, HTTPException, Body, Request
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import os
import json
from datetime import datetime
import uuid



BASE_URL = "http://localhost:8000"
# Permitir transporte no seguro para entorno de desarrollo
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


# Configuración de archivos y scopes
CREDENTIALS_FILE = ""
TOKEN_FILE = "token.json"
SCOPES = [

    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.readonly'
]

app = FastAPI()

# Instancia de Flow para manejar la autenticación OAuth
def create_flow():
    return Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri= f"{BASE_URL}/auth/callback"
    )


def get_google_service():
    if not os.path.exists(TOKEN_FILE):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


 
NOTIFICATIONS_URL = ""

@app.get("/")
def read_root():
    return {"message": "Bienvenido a la integración con Google Calendar usando "}

@app.get("/auth")
def auth_google():
    # Inicia el flujo de OAuth
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    # Guardamos el estado para validarlo en el callback
    with open("state.json", "w") as state_file:
        json.dump({"state": state}, state_file)
    return RedirectResponse(authorization_url)

@app.get("/auth/callback")
def callback(request: Request):
    # Cargamos el estado guardado
    with open("state.json", "r") as state_file:
        saved_state = json.load(state_file)["state"]

    flow = create_flow()
    
    # Validamos el estado antes de obtener el token
    state_in_response = request.query_params.get("state")
    if state_in_response != saved_state:
        raise HTTPException(status_code=400, detail="Estado de OAuth no válido.")

    # Recupera el token de la URL de autorización
    flow.fetch_token(authorization_response=str(request.url))

    creds = flow.credentials

    # Guardamos las credenciales para uso futuro
    with open(TOKEN_FILE, "w") as token_file:
        token_file.write(creds.to_json())

    return {"message": "Autenticación exitosa con Google Calendar"}


@app.get("/v1/googlecalendar/events/search", tags=["Google calendar"], summary="Search", description="Search meets by calendarID" )
def list_events():
    if not os.path.exists(TOKEN_FILE):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    service = build("calendar", "v3", credentials=creds)
    events_result = service.events().list(
        calendarId='primary', singleEvents=True,
        orderBy='startTime', maxResults=10
    ).execute()

    events = events_result.get('items', [])

    if not events:
        return {"message": "No se encontraron eventos"}
    
    return {"events": events}

@app.get("/v1/googlecalendar/calendars/list", tags=["Google calendar"], summary="List Calendars", description="List all calendars with their information.")
def list_all_calendars():
    if not os.path.exists("token.json"):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    # Cargar las credenciales desde el archivo de token
    creds = Credentials.from_authorized_user_file("token.json", SCOPES )

    # Construir el servicio de la API de Google Calendar
    service = build("calendar", "v3", credentials=creds)

    try:
        # Obtener la lista de calendarios
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])

        # Si no hay calendarios, devolvemos un mensaje
        if not calendars:
            return {"message": "No se encontraron calendarios"}

        # Devolver la información de cada calendario
        result = []
        for calendar in calendars:
            result.append({
                "id": calendar.get("id"),
                "summary": calendar.get("summary"),
                "timeZone": calendar.get("timeZone"),
                "description": calendar.get("description"),
                "accessRole": calendar.get("accessRole"),
            })

        return {"calendars": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al listar los calendarios: {str(e)}")



@app.post("/v1/googlecalendar/calendars/create", tags=["Google calendar"], summary="Create Calendar", description="Create a new calendar linked to your integration.")
def create_calendar(
    summary: str = Body(..., description="Título o nombre del nuevo calendario"),
    description: str = Body(None, description="Descripción del nuevo calendario"),
    timezone: str = Body("UTC", description="Zona horaria del nuevo calendario")
):
    if not os.path.exists("token.json"):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar"])
    service = build("calendar", "v3", credentials=creds)

    try:
        calendar = {
            'summary': summary,
            'description': description,
            'timeZone': timezone
        }

        created_calendar = service.calendars().insert(body=calendar).execute()

        # Suscribir a notificaciones para este calendario
        channel_id = str(uuid.uuid4())
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": NOTIFICATIONS_URL,  # Cambia esto por tu URL pública y segura
            "params": {
                "ttl": "86400"  # Tiempo de vida en segundos (1 hora). Ajusta según tus necesidades
            }
        }

        # Suscribirse al canal
        watch_response = service.events().watch(calendarId=created_calendar["id"], body=body).execute()

        return {
            "message": "Calendario creado y suscrito con éxito",
            "calendarId": created_calendar.get("id"),
            "calendarLink": created_calendar.get("selfLink"),
            "watchResponse": watch_response
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear el calendario o suscribirse: {str(e)}")

@app.put("/v1/googlecalendar/events/update/{event_id}", tags=["Google calendar"], summary="Update", description="Update event by event ID")
def update_event(
    event_id: str,
    summary: str = Body(None, description="Nuevo resumen o título del evento"),
    start_time: str = Body(None, description="Nueva fecha y hora de inicio en formato ISO 8601"),
    end_time: str = Body(None, description="Nueva fecha y hora de fin en formato ISO 8601"),
    timezone: str = Body("UTC", description="Zona horaria del evento")
):
    if not os.path.exists("token.json"):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar"])
    service = build("calendar", "v3", credentials=creds)

    try:
        # Recuperar el evento existente
        event = service.events().get(calendarId='primary', eventId=event_id).execute()

        # Actualizar los campos del evento si se proporcionaron
        if summary:
            event['summary'] = summary

        if start_time:
            event['start']['dateTime'] = start_time

        if end_time:
            event['end']['dateTime'] = end_time

        event['start']['timeZone'] = timezone
        event['end']['timeZone'] = timezone

        # Actualizar el evento
        updated_event = service.events().update(
            calendarId='primary', eventId=event_id, body=event
        ).execute()
        
        return {"message": "Evento actualizado", "eventLink": updated_event.get("htmlLink")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al actualizar el evento: {str(e)}")

 

#     def delete_event(self, event_id):
#         self.service.events().delete(calendarId='primary', eventId=event_id).execute()
#         return True
    




@app.post("/v1/googlecalendar/create", tags=["Google calendar"], summary="Create", description="Create a new event in the calendar.")
def create_event(
    summary: str = Body(..., description="Resumen o título del evento"),
    start_time: str = Body(..., description="Fecha y hora de inicio en formato ISO 8601"),
    end_time: str = Body(..., description="Fecha y hora de fin en formato ISO 8601"),
    timezone: str = Body("UTC", description="Zona horaria del evento")             
    ):
    if not os.path.exists("token.json"):
        raise HTTPException(status_code=400, detail="No hay token de autenticación, inicia sesión primero en /auth")

    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar"])

    service = build("calendar", "v3", credentials=creds)

    event = {
        'summary': summary,
        'start': {
            'dateTime': start_time,
            'timeZone': timezone,
        },
        'end': {
            'dateTime': end_time,
            'timeZone': timezone,
        }
    }

    try:
        event = service.events().insert(calendarId="primary", body=event).execute()
        return {"message": "Evento creado", "eventLink": event.get("htmlLink")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear el evento: {str(e)}")



@app.post("/v1/googlecalendar/notifications", tags=["Google calendar"], summary="Receive Notifications", description="Endpoint to receive calendar change notifications.")
async def receive_notifications(request: Request):
    
    try: 
        body = await request.body()
        print("🐍 File: GoogleCalendarAPI/main.py | Line: 274 | receive_notifications ~ body",body)
    except Exception as e:
        print(e)
    
    try:
        headers = request.headers
        
        
        print(headers, "\n"*2)
        
         
        # Validar la notificación según las cabeceras
        # Por ejemplo, puedes revisar 'X-Goog-Resource-ID' para saber cuál calendario fue afectado
        resource_id = headers.get("X-Goog-Resource-ID")
        resource_state = headers.get("X-Goog-Resource-State")
        if resource_state == "sync":
            return {"message": "Sincronización inicial completada."}

        # Procesar otros tipos de cambios (creación, actualización, eliminación)
        if resource_state in ["exists", "deleted"]:
            # Lógica para manejar cambios en eventos
            print(f"Cambio detectado en el recurso {resource_id}: {resource_state}")

        return {"message": "Notificación recibida con éxito"}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"Error al procesar la notificación: {str(e)}")

@app.post("/v1/googlecalendar/subscription/renew")
def renew_subscription(calendar_id: str):
    try:
        # Obtener el servicio de Google Calendar
        service = get_google_service()

        # Generar un nuevo ID de canal
        channel_id = str(uuid.uuid4())

        # Configurar el cuerpo de la solicitud para renovar la suscripción
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": NOTIFICATIONS_URL,  # Asegúrate de que sea una URL HTTPS válida y accesible
            "params": {
                "ttl": "86400"  # 24 horas en segundos (tiempo máximo permitido por Google)
            }
        }

        # Configurar el watch en el calendario específico
        watch_response = service.events().watch(calendarId=calendar_id, body=body).execute()

        # Guardar la información de la suscripción (puedes usar una base de datos en producción)
        with open("subscription_info.json", "w") as f:
            json.dump({
                "channel_id": channel_id,
                "resource_id": watch_response.get("resourceId"),
                "expiration": watch_response.get("expiration"),
                "calendar_id": calendar_id
            }, f)

        return {"message": "Suscripción renovada con éxito", "watch_response": watch_response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al renovar la suscripción: {str(e)}")
 