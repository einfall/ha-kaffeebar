"""Authenticated review API: retained phases only, no device or arbitrary I/O."""
import json
try:
    import probatio as vol  # HA 2026.10 uses native probatio WS schemas.
except ImportError:
    import voluptuous as vol  # Older supported HA versions.
from homeassistant.components import websocket_api
from homeassistant.core import callback
from .const import DOMAIN
from .review import LABELS

def positive_id(value):
    if type(value) is not int or value <= 0:
        raise vol.Invalid('Invalid event id')
    return value

POSITIVE_ID = positive_id
EVENT = {'id':POSITIVE_ID, 'started_at':str, 'classification':vol.In(LABELS)}
PAYLOADS = {
    'get':vol.Schema({}),
    'label':vol.Schema({'event_id':POSITIVE_ID,'started_at':str,'expected_classification':vol.In(LABELS),'classification':vol.In(LABELS)},required=True),
    'recipe':vol.Schema({'events':vol.All([vol.Schema(EVENT,required=True)],vol.Length(min=2,max=11)),
                         'cups':vol.All(POSITIVE_ID,vol.Range(min=1,max=10))},required=True),
    'unrecipe':vol.Schema({'event_id':POSITIVE_ID,'started_at':str},required=True),
    'save_profile':vol.Schema({'name':vol.All(str,vol.Length(min=1,max=80)),'expected_proposal':dict},required=True),
}

@callback
def async_register(hass):
    websocket_api.async_register_command(hass, ws_review)

@websocket_api.websocket_command({vol.Required('type'):'coffee_activity/review',
    vol.Optional('entry_id'):str, vol.Optional('action',default='get'):vol.In(PAYLOADS),
    vol.Optional('payload',default={}):dict})
@websocket_api.async_response
async def ws_review(hass, connection, msg):
    if connection.user is None or not connection.user.is_admin:
        connection.send_error(msg['id'],'unauthorized','Die Kaffee-Auswertung benötigt Administratorrechte.')
        return
    entries = [c for c in hass.data.get(DOMAIN,{}).values() if hasattr(c,'review_data') and not c._closed]
    if 'entry_id' not in msg and msg['action'] == 'get':
        connection.send_result(msg['id'],{'detectors':[{'entry_id':c.entry.entry_id,'name':c.entry.title,'power_entity':c.power_entity} for c in entries]})
        return
    coordinator = next((c for c in entries if c.entry.entry_id == msg.get('entry_id')),None)
    if coordinator is None:
        connection.send_error(msg['id'],'not_loaded','Kaffee-Integration ist nicht geladen. Ansicht aktualisieren.')
        return
    try:
        if len(json.dumps(msg['payload'],allow_nan=False)) > 32768:
            raise ValueError('Anfrage zu groß.')
        payload = PAYLOADS[msg['action']](msg['payload'])
        if msg['action'] == 'get':
            result = coordinator.review_data()
        else:
            result = await coordinator.async_review(msg['action'],payload)
        connection.send_result(msg['id'],result)
    except (ValueError,TypeError,vol.Invalid) as error:
        connection.send_error(msg['id'],'invalid_review',str(error)[:240])
    except Exception:
        connection.send_error(msg['id'],'save_failed','Speichern fehlgeschlagen. Ansicht aktualisieren; keine erfolgreiche Korrektur bestätigt.')
