"""Serve the review card and register it with both frontend and Lovelace."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection

from .websocket_api import async_register
from .const import REVIEW_VERSION

URL = '/local/coffee-activity/coffee-review-card.js'
LEGACY_URL = '/coffee_activity_static/coffee-review-card.js'


async def async_setup_frontend(hass):
    if hass.data.get('coffee_activity_review_registered'):
        return
    asset_path = str(Path(__file__).parent / 'frontend' / 'coffee-review-card.js')
    # A new installation may not have a /local route yet. Serve the exact
    # bundled asset URL independently of the optional www directory.
    await hass.http.async_register_static_paths([
        StaticPathConfig(URL, asset_path, False),
        StaticPathConfig(LEGACY_URL, asset_path, False),
    ])
    await hass.async_add_executor_job(
        publish_card, Path(hass.config.path('www', 'coffee-activity', 'coffee-review-card.js'))
    )
    versioned_url = f'{URL}?v={REVIEW_VERSION}'
    add_extra_js_url(hass, versioned_url)
    await async_register_lovelace_resource(hass, versioned_url)
    async_register(hass)
    hass.data['coffee_activity_review_registered'] = True


async def async_register_lovelace_resource(hass, versioned_url):
    """Use HA's collection API; leave other resources and YAML lists intact."""
    lovelace = hass.data['lovelace']
    resources = lovelace.resources
    if not isinstance(resources, ResourceStorageCollection):
        # Explicit YAML resources remain user-managed; the frontend module URL
        # above also serves dashboards using YAML resources.
        return
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    matching = []
    for item in resources.async_items():
        parsed = urlsplit(item.get('url', ''))
        if not parsed.scheme and not parsed.netloc and parsed.path in (URL, LEGACY_URL):
            matching.append(item)
    if not matching:
        await resources.async_create_item({'res_type': 'module', 'url': versioned_url})
        return
    for item in matching:
        if item.get('url') != versioned_url or item.get('type') != 'module':
            await resources.async_update_item(item['id'], {'res_type': 'module', 'url': versioned_url})


def publish_card(destination):
    """Publish only the public JS asset; update atomically when its content changes."""
    content = (Path(__file__).parent / 'frontend' / 'coffee-review-card.js').read_bytes()
    if destination.is_file() and destination.read_bytes() == content:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.js.tmp')
    try:
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
