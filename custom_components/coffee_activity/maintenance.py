"""Bounded local repair requests; never control a machine or invent aggregate cups.

Requests live outside the shareable component. Core applies them on an explicitly
requested reload/restart and persists results through the normal integration store.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

def read_request(path):
    path = Path(path)
    if not path.is_file():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('Repair request too large')
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('power_entity'), str)
            or len(data.get('corrections', [])) > 20):
        raise ValueError('Invalid repair request')
    return {'data': data, 'sha': hashlib.sha256(raw).hexdigest()}

async def async_read_request(hass, entry_id):
    config = getattr(hass, 'config', None)
    if config is None:
        return None
    path = config.path('coffee_activity', 'maintenance', entry_id+'.json')
    try:
        return await hass.async_add_executor_job(read_request, path)
    except (OSError, ValueError, TypeError):
        _LOGGER.warning('Ignoring an invalid coffee maintenance request')
        return None

def requested_options(configuration, request, power_entity):
    if (not request or request['data'].get('power_entity') != power_entity
            or configuration.get('maintenance_options_sha') == request['sha']):
        return configuration
    mode = request['data'].get('cappuccino_match_mode')
    if mode not in ('exact', 'at_least'):
        return configuration
    result = deepcopy(configuration)
    result['features']['cappuccino_match_mode'] = mode
    result['maintenance_options_sha'] = request['sha']
    return result

def apply_request(engine, request, power_entity, now):
    if (not request or request['data'].get('power_entity') != power_entity
            or engine.metadata.get('maintenance_request_sha') == request['sha']):
        return
    data = request['data']
    recovered = data.get('archived_snapshot')
    ledger = engine.cappuccino_ledger
    if (isinstance(recovered, dict) and recovered.get('total') == ledger.archived
            and recovered.get('through') == ledger.through):
        # Redistribute an EXISTING subtotal only; total must not change.
        days = {}; seen = set(); valid = True
        groups = recovered.get('groups', [])
        if not isinstance(groups, list) or len(groups) > 100:
            valid = False; groups = []
        for group in groups:
            try:
                ident = group['id']; cups = group['cups']; ids = group['event_ids']
                stamp = datetime.fromisoformat(group['started_at'])
                if (type(ident) is not int or ident <= 0 or ident in seen
                        or type(cups) is not int or not 0 <= cups <= 10
                        or stamp.utcoffset() is None or stamp > now
                        or not isinstance(ids, list) or not 1 <= len(ids) <= 20
                        or ident not in ids or any(type(i) is not int or not 0 < i <= ledger.through for i in ids)):
                    raise ValueError('Invalid archived group')
                day = stamp.astimezone(engine.timezone).date().isoformat()
                days[day] = days.get(day, 0)+cups; seen.add(ident)
            except (KeyError, ValueError, TypeError):
                valid = False; break
        if valid and sum(days.values()) == ledger.archived:
            ledger.archived_by_day = days
            engine.metadata['cappuccino_daily_recovery'] = 'Matched recorded preparation groups; archived total unchanged'
    for correction in data.get('corrections', []):
        if not isinstance(correction, dict):
            continue
        event = next((e for e in engine.events if e['id'] == correction.get('id')), None)
        if (event is None or event.get('corrections')
                or event.get('started_at') != correction.get('started_at')
                or event.get('classification') != correction.get('from')
                or event.get('day') not in engine.daily):
            continue
        label = correction.get('to')
        if label not in ('espresso', 'milk', 'heating', 'rinsing', 'unknown', 'unclear'):
            continue
        if engine.correct_event(event['id'], label, now):
            event['corrections'][-1]['source'] = 'user_confirmed_repair'
            event['corrections'][-1]['note'] = str(correction.get('note', 'Confirmed correction'))[:200]
    engine.metadata['maintenance_request_sha'] = request['sha']
