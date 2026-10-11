"""Bounded human labels, explainable checks and conservative profile proposals."""
from copy import deepcopy
from datetime import datetime
import math

LABELS = ('espresso', 'milk', 'heating', 'rinsing', 'unknown', 'unclear')
MAX_LABELS = 90
MAX_RECIPES = 30

def aware(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None

def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0

def manual_recipe(value):
    """Return only validated, bounded annotations, including after storage restore."""
    if not isinstance(value, dict):
        return None
    ids = value.get('event_ids')
    start, end = aware(value.get('started_at')), aware(value.get('ended_at'))
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 11
            or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)
            or value.get('id') != min(ids) or type(value.get('cups')) is not int
            or not 1 <= value['cups'] <= 10 or start is None or end is None or end < start
            or (end-start).total_seconds() > 3600 or not isinstance(value.get('profile'), str)):
        return None
    return {k: deepcopy(value.get(k)) for k in ('id', 'event_ids', 'cups', 'started_at', 'ended_at', 'profile', 'at')}

def classify(event, options, standalone=False):
    """Replay recorded phase features; absent pump samples cannot be fabricated."""
    d = event['duration_s']; marker = event.get('espresso_marker', False)
    context = event.get('milk_context', False)
    onset = event.get('first_espresso_marker_s')
    late = (context and marker and number(onset) and onset >= max(options.espresso_max_s, options.milk_heating_min_s)
            and 0 <= d-onset <= options.milk_pump_min_s and event['peak_w'] >= options.milk_heating_min_w
            and options.milk_heating_min_s <= d <= options.milk_heating_max_s)
    espresso = marker and options.espresso_min_s <= d <= options.espresso_max_s
    milk = not espresso and (((context or standalone) and not marker and event.get('milk_marker', False)
                             and options.milk_pump_min_s <= d <= options.milk_pump_max_s)
             or (context and (not marker or late) and event['peak_w'] >= options.milk_heating_min_w
                 and options.milk_heating_min_s <= d <= options.milk_heating_max_s))
    return 'espresso' if espresso else 'milk' if milk else 'unclear' if marker and options.espresso_max_s < d <= max(options.milk_pump_max_s, options.milk_heating_max_s) else 'heating' if event['peak_w'] >= options.milk_heating_min_w else 'unknown'

def checks(event, options):
    d = event.get('duration_s', 0); peak = event.get('peak_w', 0)
    rows = [
        {'name': 'Espresso-Pumpensignal', 'observed': 'ja' if event.get('espresso_marker') else 'nein',
         'required': f'{options.espresso_pump_min_w:g}–{options.espresso_pump_max_w:g} W', 'passed': bool(event.get('espresso_marker'))},
        {'name': 'Espresso-Dauer', 'observed': f'{d:g} s', 'required': f'{options.espresso_min_s:g}–{options.espresso_max_s:g} s',
         'passed': options.espresso_min_s <= d <= options.espresso_max_s},
        {'name': 'Milch-Pumpensignal', 'observed': 'ja' if event.get('milk_marker') else 'nein',
         'required': f'{options.milk_pump_min_w:g}–{options.milk_pump_max_w:g} W', 'passed': bool(event.get('milk_marker'))},
        {'name': 'Milch-Pumpendauer', 'observed': f'{d:g} s', 'required': f'{options.milk_pump_min_s:g}–{options.milk_pump_max_s:g} s',
         'passed': options.milk_pump_min_s <= d <= options.milk_pump_max_s},
        {'name': 'Heiz-/Dampfspitze', 'observed': f'{peak:g} W', 'required': f'ab {options.milk_heating_min_w:g} W',
         'passed': peak >= options.milk_heating_min_w},
        {'name': 'Heiz-/Dampfdauer', 'observed': f'{d:g} s', 'required': f'{options.milk_heating_min_s:g}–{options.milk_heating_max_s:g} s',
         'passed': options.milk_heating_min_s <= d <= options.milk_heating_max_s},
        {'name': 'Espresso davor', 'observed': 'ja' if event.get('milk_context') else 'nein',
         'required': f'Espresso innerhalb {options.milk_after_espresso_s:g} s (für Heiz-/Dampfregel)', 'passed': bool(event.get('milk_context'))},
    ]
    onset = event.get('first_espresso_marker_s')
    if number(onset):
        rows.append({'name': 'Späte Nachfüllpumpe', 'observed': f'Beginn nach {onset:g} s; Rest {max(0,d-onset):g} s',
                     'required': f'Beginn ab {max(options.espresso_max_s,options.milk_heating_min_s):g} s; Rest höchstens {options.milk_pump_min_s:g} s',
                     'passed': onset >= max(options.espresso_max_s,options.milk_heating_min_s) and 0 <= d-onset <= options.milk_pump_min_s})
    return rows

class ReviewHistory:
    def __init__(self):
        self.labels = []
        self.recipes = []

    def export(self):
        return {'labels': deepcopy(self.labels), 'recipes': deepcopy(self.recipes)}

    def restore(self, raw):
        self.labels, self.recipes = [], []
        if not isinstance(raw, dict):
            return
        labels = raw.get('labels', [])
        for row in labels[-MAX_LABELS:] if isinstance(labels, list) else []:
            if (isinstance(row, dict) and row.get('label') in LABELS and type(row.get('id')) is int
                    and aware(row.get('started_at')) and number(row.get('duration_s')) and number(row.get('peak_w'))
                    and isinstance(row.get('profile'), str) and isinstance(row.get('power_entity'), str)):
                self.labels.append(deepcopy(row))
        recipes = raw.get('recipes', [])
        for row in recipes[-MAX_RECIPES:] if isinstance(recipes, list) else []:
            valid = manual_recipe(row)
            if valid and isinstance(row.get('power_entity'), str):
                self.recipes.append({**valid, 'power_entity': row['power_entity']})

    def label(self, event, kind, source, now):
        if kind not in LABELS:
            raise ValueError('Ungültige Kategorie.')
        self.labels = [r for r in self.labels if (r['id'], r['started_at'], r['power_entity']) != (event['id'], event['started_at'], source)]
        keys = ('id', 'started_at', 'duration_s', 'peak_w', 'profile', 'espresso_marker', 'milk_marker',
                'milk_context', 'first_espresso_marker_s', 'measurement_quality')
        self.labels.append({**{k: deepcopy(event.get(k)) for k in keys}, 'label': kind, 'power_entity': source, 'at': now.isoformat()})
        self.labels = self.labels[-MAX_LABELS:]

    def confirm_recipe(self, events, cups, gap_s, source, now):
        if type(cups) is not int or not 1 <= cups <= 10 or not 2 <= len(events) <= 11:
            raise ValueError('Rezept benötigt einen Espresso, Milchphasen und 1–10 Tassen.')
        ordered = sorted(events, key=lambda e: e['started_at'])
        if any(aware(e.get('started_at')) is None or aware(e.get('ended_at')) is None
               or aware(e['ended_at']) < aware(e['started_at']) for e in ordered):
            raise ValueError('Ungültige Phasenzeitspanne.')
        if (len({e['id'] for e in ordered}) != len(ordered) or ordered[0]['classification'] != 'espresso'
                or any(e['classification'] != 'milk' for e in ordered[1:])
                or len({e.get('profile') for e in ordered}) != 1):
            raise ValueError('Genau einen Espresso und danach Milchphasen desselben Profils auswählen.')
        if any(e.get('manual_recipe') for e in ordered):
            raise ValueError('Eine Phase gehört bereits zu einer bestätigten Zubereitung. Diese zuerst lösen.')
        if any(e.get('power_entity') and e['power_entity'] != source for e in ordered):
            raise ValueError('Eine Phase stammt von einer anderen Leistungsquelle.')
        for a,b in zip(ordered, ordered[1:]):
            delta = (aware(b['started_at'])-aware(a['ended_at'])).total_seconds()
            if not 0 <= delta <= gap_s:
                raise ValueError('Phasen überlappen oder liegen außerhalb des Zuordnungsfensters.')
        annotation = manual_recipe({'id': min(e['id'] for e in ordered), 'event_ids': [e['id'] for e in ordered],
                 'cups': cups, 'started_at': ordered[0]['started_at'], 'ended_at': ordered[-1]['ended_at'],
                 'profile': ordered[0]['profile'], 'at': now.isoformat()})
        if annotation is None:
            raise ValueError('Ungültige Zeitspanne für eine Zubereitung.')
        for event in ordered:
            event['manual_recipe'] = deepcopy(annotation)
            event['reviewed_at'] = now.isoformat()
            self.label(event, event['classification'], event.get('power_entity',''), now)
        self.recipes.append({**annotation, 'power_entity': source})
        self.recipes = self.recipes[-MAX_RECIPES:]
        return annotation

    def proposal(self, options, profile, source, features):
        from .engine import DetectionOptions
        records = [r for r in self.labels if r['profile'] == profile and r['power_entity'] == source
                   and r.get('measurement_quality') not in ('missing', 'stale', 'sparse')
                   and number(r.get('duration_s')) and number(r.get('peak_w'))]
        counts = {kind: sum(r['label'] == kind for r in records) for kind in LABELS}
        thresholds = options.as_dict(); changes = []; notes = []
        for kind, low, high in (('espresso','espresso_min_s','espresso_max_s'), ('milk','milk_heating_min_s','milk_heating_max_s'), ('milk','milk_pump_min_s','milk_pump_max_s')):
            rows = [r for r in records if r['label'] == kind and
                    (r.get('espresso_marker') if kind == 'espresso' else r.get('milk_context') and
                     (r['peak_w'] >= options.milk_heating_min_w if low == 'milk_heating_min_s' else r.get('milk_marker') and not r.get('espresso_marker') and r['peak_w'] < options.milk_heating_min_w))]
            if len(rows) < 3:
                continue
            durations = [r['duration_s'] for r in rows]
            thresholds[low] = max(1, min(thresholds[low], math.floor(min(durations)*.9)))
            thresholds[high] = max(thresholds[high], math.ceil(max(durations)*1.1))
        candidate = DetectionOptions.from_mapping(thresholds)
        mismatches = [r['id'] for r in records if r['label'] in ('espresso','milk') and classify(r,candidate,features['standalone_milk']) != r['label']
                      or r['label'] not in ('espresso','milk') and classify(r,candidate,features['standalone_milk']) in ('espresso','milk')]
        if mismatches:
            thresholds = options.as_dict()
            notes.append('Leistungsmerkmale/Kontext widersprechen bestätigten Kategorien; keine Grenzänderung. Geführte Aufnahme verwenden.')
        for key,value in thresholds.items():
            if value != options.as_dict()[key]:
                changes.append({'key':key,'before': options.as_dict()[key],'after':value})
        recipes = [r for r in self.recipes if r['profile'] == profile and r['power_entity'] == source]
        recipe = None
        if recipes:
            last = recipes[-1]; pairs = {(len(r['event_ids'])-1,r['cups']) for r in recipes[-3:]}
            if len(pairs) == 1:
                recipe = {**features, 'cappuccino_enabled':True, 'cappuccino_milk_phases':len(last['event_ids'])-1, 'cappuccino_cups':last['cups']}
            else:
                notes.append('Bestätigte Rezepte widersprechen sich; keine automatische Rezeptänderung.')
        recipe_changed = recipe is not None and recipe != features
        if not changes and not recipe_changed:
            notes.append('Noch kein geänderter Vorschlag: mindestens drei geeignete Bestätigungen je Phasentyp nötig; vorhandenes Rezept kann bereits passen.')
        notes.append('Wattgrenzen bleiben unverändert: Phasenspitzen ersetzen keine Pumpenmessreihe. Originalprofil bleibt erhalten.')
        return {'ready':bool(changes or recipe_changed), 'counts':counts, 'changes':changes, 'thresholds':thresholds,
                'features':recipe or deepcopy(features), 'recipe_examples':len(recipes), 'notes':notes}
