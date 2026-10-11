/* Local Coffee Activity review card. No libraries, remote assets or credentials. */
// Extra modules may finish before HA installs its scoped custom-element registry.
// Wait before declaring the class as well, so it uses HA's current HTMLElement.
customElements.whenDefined('home-assistant').then(() => {
const CA_LABELS = {espresso:'Espresso',milk:'Milchphase',heating:'Heizen',rinsing:'Spülen',unknown:'Unbekannt',unclear:'Unklar'};
const CA_ICONS = {espresso:'coffee',milk:'cup-water',heating:'radiator',rinsing:'water',unknown:'help-circle-outline',unclear:'help-circle-outline'};
const CA_ESCAPE = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
class CoffeeReviewCard extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:'open'}); this._selected = new Set(); this._tab='phases'; this._day='today';
    this._filter='all'; this._pending=null; this._loading=false; this._saving=false; this._message=''; this._open=new Set(); this._groupFocus=null;
    this.shadowRoot.addEventListener('click',e=>this._click(e));
    this.shadowRoot.addEventListener('change',e=>this._change(e));
    this.shadowRoot.addEventListener('dragstart',e=>this._dragStart(e));
    this.shadowRoot.addEventListener('dragover',e=>{const t=e.target.closest('[data-drop]'); if(t&&!this._saving&&!this._data?.busy){e.preventDefault();e.dataTransfer.dropEffect='move';}});
    this.shadowRoot.addEventListener('drop',e=>this._drop(e));
    this.shadowRoot.addEventListener('toggle',e=>{const id=e.target.dataset?.detail;if(id){if(e.target.open)this._open.add(Number(id));else this._open.delete(Number(id));}},true);
  }
  setConfig(config) {this._config={...config}; this._entry=config.entry_id;this._render();if(this.isConnected)this._refresh();}
  set hass(value) {this._hass=value;if(!this._data&&!this._loading&&this.isConnected)this._refresh();}
  getCardSize(){return 12;}
  getGridOptions(){return {columns:'full',min_columns:6};}
  static getStubConfig(){return {type:'custom:coffee-review-card'};}
  connectedCallback(){this._render();this._refresh();if(!this._timer)this._timer=setInterval(()=>{if(!this._pending&&!this._saving&&!['INPUT','SELECT'].includes(this.shadowRoot.activeElement?.tagName))this._refresh();},15000);}
  disconnectedCallback(){clearInterval(this._timer);this._timer=null;}
  async _request(action='get',payload={}) {
    return this._hass.callWS({type:'coffee_activity/review',...(this._entry?{entry_id:this._entry}:{}),action,payload});
  }
  async _refresh(){
    if(!this._hass||this._loading||this._saving)return;
    this._loading=true;
    try {
      if(!this._entry){const list=await this._request();this._detectors=list.detectors||[];if(this._detectors.length===1)this._entry=this._detectors[0].entry_id;}
      if(this._entry){this._data=await this._request();this._selected=new Set([...this._selected].filter(id=>this._data.events.some(e=>e.id===id)));if(this._groupFocus!==null&&!this._group(this._groupFocus))this._groupFocus=null;}
      this._error='';
    } catch(error){this._error=(error.code==='unknown_command'||error.code==='not_loaded')?'Die neue Kaffee-Auswertung ist noch nicht geladen. Nach dem Core-Neustart diese Seite aktualisieren.':(error.message||'Auswertung konnte nicht geladen werden.');}
    finally{this._loading=false;if(this.isConnected)this._render();}
  }
  _date(){if(!this._data)return '';if(this._day==='today')return this._data.today;if(this._day==='yesterday'){const d=new Date(this._data.today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()-1);return d.toISOString().slice(0,10);}return this._day;}
  _time(value,date=false){if(!value)return '–';const d=new Date(value);if(Number.isNaN(d.getTime()))return '–';return new Intl.DateTimeFormat('de-DE',{timeZone:this._data?.timezone||'Europe/Berlin',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',...(date?{day:'2-digit',month:'2-digit'}:{})}).format(d);}
  _range(event){const start=new Date(event.started_at),end=new Date(event.ended_at);const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:this._data?.timezone||'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'});return this._time(event.started_at,true)+' – '+this._time(event.ended_at,fmt.format(start)!==fmt.format(end));}
  _localDay(value){const date=new Date(value);if(Number.isNaN(date.getTime()))return '';const parts=new Intl.DateTimeFormat('en-CA',{timeZone:this._data?.timezone||'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);const part=k=>parts.find(p=>p.type===k)?.value;return `${part('year')}-${part('month')}-${part('day')}`;}
  _group(id){return this._data?.groups?.find(group=>group.id===Number(id));}
  _groupSpan(group){const manual=group.manual_recipe,start=new Date(manual?.started_at).getTime(),end=new Date(manual?.ended_at).getTime();return manual&&typeof manual.started_at==='string'&&typeof manual.ended_at==='string'&&Number.isFinite(start)&&Number.isFinite(end)&&end>=start?{started_at:manual.started_at,ended_at:manual.ended_at}:group;}
  _groups(){return (this._data?.groups||[]).filter(group=>this._localDay(this._groupSpan(group).started_at)===this._date()).sort((a,b)=>new Date(this._groupSpan(b).started_at)-new Date(this._groupSpan(a).started_at));}
  _events(){const group=this._groupFocus!==null?this._group(this._groupFocus):null;return (this._data?.events||[]).filter(e=>group?group.event_ids.includes(e.id):e.day===this._date()).sort((a,b)=>new Date(a.started_at)-new Date(b.started_at));}
  _phase(id){return this._data?.events.find(e=>e.id===Number(id));}
  _groupEvents(group){return (group?.event_ids||[]).map(id=>this._phase(id)).filter(Boolean).sort((a,b)=>new Date(a.started_at)-new Date(b.started_at));}
  _recipeProblem(events){
    if(events.length<2||events.length>11||events[0]?.classification!=='espresso'||events.slice(1).some(e=>e.classification!=='milk'))return 'Genau einen Espresso und danach eine bis zehn Milchphasen auswählen. Unbekannte Phasen zuerst zuordnen.';
    if(events.some(e=>e.manual_recipe))return 'Eine Phase ist schon einer bestätigten Zubereitung zugeordnet. Diese Bestätigung zuerst lösen.';
    if(new Set(events.map(e=>e.profile)).size!==1)return 'Die Phasen müssen vom selben Profil stammen.';
    if(events.some(e=>e.power_entity&&e.power_entity!==this._data.power_entity))return 'Die Phasen müssen von dieser Leistungsquelle stammen.';
    const gap=this._data.features.session_gap_s;
    for(let i=0;i<events.length;i++){
      const start=new Date(events[i].started_at).getTime(),end=new Date(events[i].ended_at).getTime();
      if(!Number.isFinite(start)||!Number.isFinite(end)||end<start)return 'Eine Phasenzeit ist nicht gültig. Ansicht aktualisieren.';
      if(i){const pause=(start-new Date(events[i-1].ended_at).getTime())/1000;if(pause<0||pause>gap)return 'Die Phasen überlappen oder liegen außerhalb des Zuordnungsfensters.';}
    }
    if(new Date(events.at(-1).ended_at)-new Date(events[0].started_at)>3600000)return 'Eine bestätigte Zubereitung darf höchstens eine Stunde umfassen.';
    return '';
  }
  _stageRecipe(events){
    if(this._saving||this._data?.busy)return;
    const ordered=[...events].sort((a,b)=>new Date(a.started_at)-new Date(b.started_at)),problem=this._recipeProblem(ordered);
    if(problem){this._message=problem;this._render();return;}
    this._pending={kind:'recipe',events:ordered};this._message='';this._render();this.shadowRoot.querySelector('#cups')?.focus();
  }
  _groupReview(group){
    const events=this._groupEvents(group),manual=group?.manual_recipe||events.find(e=>e.manual_recipe)?.manual_recipe;
    const ids=manual?.event_ids||group?.event_ids||[];
    const missing=ids.some(id=>!this._phase(id)),anchor=events.find(e=>e.manual_recipe);
    const problem=missing?'Zugehörige Phasen sind teilweise archiviert; diese Zuordnung kann im Detailprotokoll nicht bestätigt oder gelöst werden.':this._recipeProblem(events);
    return {events,manual,missing,anchor,problem};
  }
  _stageUnrecipe(phase){
    if(!phase||this._saving||this._data?.busy)return;
    if(!phase.manual_recipe||phase.manual_recipe.event_ids.some(id=>!this._phase(id))){this._message='Bestätigung teilweise archiviert; sie kann im Detailprotokoll nicht mehr gelöst werden.';this._render();return;}
    this._pending={kind:'unrecipe',event:phase};this._message='';this._render();this.shadowRoot.querySelector('[data-action="save"]')?.focus();
  }
  _label(id,category){const phase=this._phase(id);if(!phase||this._data.busy||this._saving)return;this._pending={kind:'label',event:phase,category};this._message='';this._render();this.shadowRoot.querySelector('[data-action="save"]')?.focus();}
  _dragStart(e){const row=e.target.closest('[data-event]');if(!row||this._data?.busy||this._saving){e.preventDefault();return;}e.dataTransfer.setData('application/x-coffee-phase',String(row.dataset.event));e.dataTransfer.effectAllowed='move';}
  _drop(e){const target=e.target.closest('[data-drop]');if(!target)return;e.preventDefault();const id=e.dataTransfer.getData('application/x-coffee-phase');if(/^\d+$/.test(id))this._label(Number(id),target.dataset.drop);}
  _change(e){
    const target=e.target;
    if(target.dataset.select){const id=Number(target.dataset.select);if(target.checked)this._selected.add(id);else this._selected.delete(id);this._render();}
    else if(target.dataset.category){this._label(target.dataset.category,target.value);}
    else if(target.id==='day'){this._day=target.value;this._selected.clear();this._pending=null;this._groupFocus=null;this._render();}
    else if(target.id==='detector'){this._entry=target.value;this._data=null;this._selected.clear();this._pending=null;this._groupFocus=null;this._refresh();}
    else if(target.id==='filter'){this._filter=target.value;this._render();this.shadowRoot.querySelector('#filter')?.focus();}
  }
  _click(e){
    const node=e.target.closest('[data-action]');if(!node||node.disabled)return;
    const action=node.dataset.action;
    if(action==='refresh')this._refresh();
    if(action==='filter'){
      const category=node.dataset.filter;
      if(!Object.prototype.hasOwnProperty.call(CA_LABELS,category))return;
      this._filter=this._filter===category?'all':category;
      this._render();
      this.shadowRoot.querySelector(`[data-action="filter"][data-filter="${category}"]`)?.focus();
    }
    if(action==='tab'){this._tab=node.dataset.tab;this._pending=null;this._render();}
    if(action==='day'){this._day=node.dataset.day;this._selected.clear();this._pending=null;this._groupFocus=null;this._render();}
    if(action==='cancel'){this._pending=null;this._render();}
    if(action==='confirm')this._label(node.dataset.id,this._phase(node.dataset.id)?.classification);
    if(action==='recipe')this._stageRecipe([...this._selected].map(id=>this._phase(id)).filter(Boolean));
    if(action==='unrecipe')this._stageUnrecipe(this._phase(node.dataset.id));
    if(action==='group-view'||action==='group-select'){
      const group=this._group(node.dataset.id);if(!group)return;
      const review=this._groupReview(group);
      this._groupFocus=group.id;this._tab='phases';this._pending=null;this._selected.clear();
      if(action==='group-select'&&!review.problem)review.events.forEach(event=>this._selected.add(event.id));
      this._render();this.shadowRoot.querySelector('[data-action="clear-group"]')?.focus();
    }
    if(action==='clear-group'){this._groupFocus=null;this._selected.clear();this._pending=null;this._render();}
    if(action==='group-confirm'){
      const group=this._group(node.dataset.id);if(!group)return;
      const review=this._groupReview(group);if(review.problem){this._message=review.problem;this._render();return;}
      this._stageRecipe(review.events);
    }
    if(action==='group-unrecipe')this._stageUnrecipe(this._groupReview(this._group(node.dataset.id)).anchor);
    if(action==='profile'){this._pending={kind:'profile'};this._render();this.shadowRoot.querySelector('#profile-name')?.focus();}
    if(action==='save')this._save();
  }
  async _save(){
    const pending=this._pending;if(!pending||this._saving||this._data?.busy)return;
    let action,payload;
    if(pending.kind==='label'){action='label';payload={event_id:pending.event.id,started_at:pending.event.started_at,expected_classification:pending.event.classification,classification:pending.category};}
    else if(pending.kind==='unrecipe'){action='unrecipe';payload={event_id:pending.event.id,started_at:pending.event.started_at};}
    else if(pending.kind==='recipe'){const input=this.shadowRoot.querySelector('#cups');if(!input?.checkValidity()){input?.reportValidity();return;}action='recipe';payload={events:pending.events.map(e=>({id:e.id,started_at:e.started_at,classification:e.classification})),cups:Number(input.value)};}
    else{action='save_profile';payload={name:this.shadowRoot.querySelector('#profile-name').value.trim(),expected_proposal:this._data.proposal};}
    this._saving=true;this._error='';this._actionError='';this._render();
    try{
      const result=await this._request(action,payload);
      this._message=result.saved_profile?`Profil „${result.saved_profile}“ gespeichert. Es ist in der Profilauswahl verfügbar; aktiv bleibt „${result.active_profile}“.`:'Gespeichert. Zähler und Zuordnung wurden aktualisiert.';
      this._pending=null;this._selected.clear();
    }catch(error){this._actionError=error.message||'Speichern fehlgeschlagen.';}
    finally{this._saving=false;await this._refresh();}
  }
  _confirmHtml(){
    const p=this._pending;if(!p)return '';
    let text='',field='';
    if(p.kind==='label')text=`${this._range(p.event)} · ${p.event.duration_s} s: ${CA_LABELS[p.event.classification]} → ${CA_LABELS[p.category]}. Diese Bestätigung aktualisiert die Zähler und dient als Lernbeispiel.${p.event.manual_recipe?' Die bisherige Cappuccino-Bestätigung wird bei einem Kategorienwechsel gelöst.':''}`;
    if(p.kind==='recipe'){text=`${p.events.map(e=>this._range(e)+' '+CA_LABELS[e.classification]).join(' · ')}. Diese Phasen gemeinsam als Cappuccino bestätigen. Die gewählte Tassenzahl zählt einmal für diese Gruppe und hat Vorrang vor der automatischen Rezeptregel. Espresso und Milch bleiben ihre eigenen Phasenkategorien.`;field='<label>Tassen <input id="cups" type="number" min="1" max="10" step="1" required value="'+this._data.features.cappuccino_cups+'"></label>';}
    if(p.kind==='unrecipe')text='Cappuccino-Bestätigung für '+this._range(p.event)+' lösen. Die Espresso-/Milchphasen bleiben erhalten; danach gilt wieder die automatische Rezeptregel.';
    if(p.kind==='profile'){text='Die unten gezeigten Änderungen als eigenes Profil speichern. Aktiv bleibt das bisherige Profil; die Auswahl des neuen Profils erfolgt anschließend in der Integration.';field='<label>Profilname <input id="profile-name" maxlength="80" value="'+CA_ESCAPE(this._data.profile+' (gelernt)')+'"></label>';}
    return `<div class="confirm" role="region" aria-label="Änderung prüfen"><strong>Änderung prüfen</strong><p>${CA_ESCAPE(text)}</p>${field}<div class="actions"><button data-action="save" ${this._saving||this._data.busy?'disabled':''}>${this._saving?'Speichert …':'Speichern'}</button><button class="secondary" data-action="cancel" ${this._saving?'disabled':''}>Abbrechen</button></div></div>`;
  }
  _phaseHtml(event){
    const disabled=this._saving||this._data.busy;
    const category=event.classification;
    const confirmed=event.reviewed_at||event.corrections?.length;
    const time=this._range(event);
    return `<article class="phase ${category}" draggable="${!disabled}" data-event="${event.id}">
      <div class="row"><input type="checkbox" data-select="${event.id}" aria-label="Phase ${CA_ESCAPE(time)} auswählen" ${this._selected.has(event.id)?'checked':''} ${disabled||event.manual_recipe?'disabled':''}>
      <ha-icon icon="mdi:${CA_ICONS[category]}"></ha-icon><div class="title"><strong>${CA_ESCAPE(CA_LABELS[category])}${confirmed?' · bestätigt':''}</strong><div class="time">${CA_ESCAPE(time)} <span>· ${event.duration_s} s · ${event.peak_w} W Spitze</span></div></div>
      <label class="sr" for="category-${event.id}">Kategorie für ${CA_ESCAPE(time)}</label><select id="category-${event.id}" data-category="${event.id}" ${disabled?'disabled':''}>${Object.entries(CA_LABELS).map(([k,v])=>`<option value="${k}" ${k===category?'selected':''}>${v}</option>`).join('')}</select></div>
      <div class="reason">${confirmed?'Automatisch: '+CA_ESCAPE(event.automatic_label)+' · ':''}${CA_ESCAPE(event.reason_label)}.</div>
      ${event.manual_recipe?`<div class="drink">☕ ${event.manual_recipe.cups} Cappuccino bestätigt ${event.id===event.manual_recipe.id?`<button class="link" data-action="unrecipe" data-id="${event.id}" ${disabled||event.manual_recipe.event_ids.some(id=>!this._phase(id))?'disabled':''}>Zuordnung lösen</button>`:''}</div>`:''}
      <details data-detail="${event.id}" ${this._open.has(event.id)?'open':''}><summary>Warum diese Erkennung? Grenzen und Verlauf</summary>
      <p class="muted">${CA_ESCAPE(event.profile)} · ${CA_ESCAPE(event.threshold_scope)} · Messqualität: ${CA_ESCAPE(event.measurement_quality||'nicht gespeichert')}<br>Quelle: ${CA_ESCAPE(event.power_entity||'nicht gespeichert; Kategorie korrigierbar, aber keine automatische Grenzanpassung daraus')}</p>
      <div class="table"><table><thead><tr><th>Merkmal</th><th>Gemessen</th><th>Regel / Zeitfenster</th></tr></thead><tbody>${event.checks.map(c=>`<tr><td><span class="${c.passed?'pass':'fail'}">${c.passed?'✓':'–'}</span> ${CA_ESCAPE(c.name)}</td><td>${CA_ESCAPE(c.observed)}</td><td>${CA_ESCAPE(c.required)}</td></tr>`).join('')}</tbody></table></div>
      <p class="muted">Pumpensignal und Dauer müssen gemeinsam passen. Die Milch-Heizregel braucht Espresso-Kontext; eine kurze späte Nachfüllpumpe ist eine Ausnahme. Milch bleibt ohne zusätzliche Messung eine Schätzung. Grün bei einem Merkmal allein bedeutet noch keinen Espresso oder Milchschaum.</p>
      ${(event.corrections||[]).map(c=>`<div class="audit">${this._time(c.at,true)}: ${CA_ESCAPE(CA_LABELS[c.from]||c.from)} → ${CA_ESCAPE(CA_LABELS[c.to]||c.to)}</div>`).join('')}
      <button class="secondary" data-action="confirm" data-id="${event.id}" ${disabled?'disabled':''}>Diese Kategorie als richtig bestätigen</button></details></article>`;
  }
  _groupStatus(group){
    if(group.manual_recipe){if(!group.recipe_matched)return {label:'Bestätigung teilweise im Detailprotokoll',kind:'archived'};return {label:`Bestätigt · ${group.cappuccino} Tassen`,kind:'matched'};}
    if(group.recipe_status!=='final')return {label:group.recipe_matched?`Vorläufig · ${group.cappuccino} Tassen`:'Noch offen',kind:'pending'};
    return {label:group.recipe_matched?`Erkannt · ${group.cappuccino} Tassen`:'Kein Cappuccino nach Rezept',kind:group.recipe_matched?'matched':'unmatched'};
  }
  _groupReasons(group){
    const f=this._data.features,review=this._groupReview(group),reasons=[];
    if(review.manual){
      if(review.missing){reasons.push('Diese manuell bestätigte Zuordnung ist teilweise archiviert: Im begrenzten Detailprotokoll fehlen zugehörige Phasen. Tages- und Gesamtzähler können die vollständige Bestätigung weiterhin enthalten.');}
      else if(group.recipe_matched){reasons.push(`Manuell bestätigt: ${group.cappuccino} Tassen für diese Gruppe. Die Bestätigung hat Vorrang vor der aktuellen automatischen Regel, auch wenn diese deaktiviert ist oder andere Mengen verlangt.`);}
      else reasons.push('Für diese Bestätigung ist im Detailprotokoll derzeit kein vollständiger Rezepttreffer verfügbar. Die Zähleranzeige ist unabhängig vom begrenzten Gruppenprotokoll.');
    }else{
      if(!f.cappuccino_enabled)reasons.push('Die automatische Cappuccino-Regel ist deaktiviert. Diese Gruppe wird automatisch nicht als Cappuccino gezählt; eine manuelle Bestätigung ist weiterhin möglich.');
      if(group.espresso!==1)reasons.push(group.espresso===0?'Nur Milchphasen: In dieser Gruppe fehlt ein Espresso. Milch alleine ergibt nach dem Rezept keinen Cappuccino.':`Die Gruppe enthält ${group.espresso} Espressobezüge; das Rezept verlangt genau einen.`);
      if(group.milk<f.cappuccino_milk_phases)reasons.push(`Zu wenige Milchphasen: ${group.milk} von ${f.cappuccino_milk_phases}; es fehlen ${f.cappuccino_milk_phases-group.milk}.`);
      if(f.cappuccino_match_mode!=='at_least'&&group.milk>f.cappuccino_milk_phases)reasons.push(`Zu viele Milchphasen für die genaue Regel: ${group.milk} statt ${f.cappuccino_milk_phases}. Im Modus „mindestens“ wäre diese Menge zulässig.`);
      if(group.recipe_matched)reasons.push(`Aktuelles Rezept erfüllt: ${group.espresso} Espresso + ${group.milk} Milchphasen → ${group.cappuccino} Tassen für diese Gruppe. Die Tassenzahl ist im Rezept festgelegt und wird nicht mit der Anzahl der Milchphasen multipliziert.`);
      if(group.recipe_status!=='final'){
        if(group.waiting_for_phase)reasons.push('Noch vorläufig: Eine laufende Phase hat innerhalb des Zuordnungsfensters begonnen und könnte dazukommen. Auch bei 0 Sekunden Restzeit wird erst nach ihrem Abschluss entschieden.');
        else reasons.push(`Noch vorläufig: Das Zuordnungsfenster ist offen (${group.recipe_wait_s??0} s verbleibend). Weitere Milchphasen können den Treffer verändern. Erst eine abgeschlossene passende Gruppe zählt im Tages- und Gesamtzähler.`);
      }else reasons.push(group.recipe_matched?'Gruppe abgeschlossen: Diese Rezeptzuordnung zählt im Tages- und Gesamtzähler.':'Gruppe abgeschlossen: Sie zählt nach dem aktuellen automatischen Rezept nicht als Cappuccino.');
    }
    const ordered=[...(this._data.groups||[])].sort((a,b)=>new Date(a.started_at)-new Date(b.started_at)),index=ordered.findIndex(item=>item.id===group.id),previous=ordered[index-1];
    if(!review.manual&&group.espresso===1)reasons.push('Dieser Espresso beginnt eine eigene Gruppe. Jeder neue Espresso trennt die automatische Zuordnung vom vorherigen Bezug.');
    if(!review.manual&&group.espresso===0&&previous){
      const pause=Math.round((new Date(group.started_at)-new Date(previous.ended_at))/1000);
      if(previous.profile!==group.profile)reasons.push(`Getrennt durch einen Profilwechsel: ${previous.profile??'unbekannt'} → ${group.profile??'unbekannt'}.`);
      else if(pause>f.session_gap_s)reasons.push(`Getrennt durch die Pause: ${pause} s zwischen den Gruppen, erlaubt sind höchstens ${f.session_gap_s} s.`);
      else if(previous.manual_recipe)reasons.push('Die vorherige Gruppe wurde bereits manuell bestätigt; weitere Milchphasen werden ihr automatisch nicht hinzugefügt.');
    }
    return reasons;
  }
  _groupHtml(group){
    const review=this._groupReview(group),state=this._groupStatus(group),disabled=this._saving||this._data.busy;
    const remaining=(review.manual?.event_ids||group.event_ids||[]).filter(id=>!this._phase(id));
    return `<article class="recipe-group ${state.kind}" data-group="${group.id}">
      <div class="group-head"><div><strong>${CA_ESCAPE(group.drink_label||(group.espresso===0?'Milchphasen ohne Espresso':group.milk===0?'Espresso ohne Milchphase':'Espresso mit Milchphasen'))}</strong><div class="time">${CA_ESCAPE(this._range(this._groupSpan(group)))}</div></div><span class="recipe-status ${state.kind}">${CA_ESCAPE(state.label)}</span></div>
      <p class="group-counts">${group.espresso} Espresso <span>+</span> ${group.milk} Milchphasen <span>·</span> ${CA_ESCAPE(group.profile||'Profil nicht gespeichert')}</p>
      <div class="recipe-reasons">${this._groupReasons(group).map(reason=>`<p>${CA_ESCAPE(reason)}</p>`).join('')}</div>
      <h4>Zugehörige Phasen</h4><ul class="group-phases">${review.events.map(event=>`<li data-phase-id="${event.id}"><ha-icon icon="mdi:${CA_ICONS[event.classification]||'help-circle-outline'}"></ha-icon><div><strong>${CA_ESCAPE(CA_LABELS[event.classification]||event.classification)}</strong><span>${CA_ESCAPE(this._range(event))} · ${CA_ESCAPE(event.duration_s)} s</span></div></li>`).join('')}${remaining.map(id=>`<li class="muted">Phase ${CA_ESCAPE(id)} · nicht mehr im Detailprotokoll</li>`).join('')}</ul>
      <div class="group-actions"><button class="secondary" data-action="group-view" data-id="${group.id}">Phasen ansehen</button>${review.manual?`<button class="secondary" data-action="group-unrecipe" data-id="${group.id}" ${disabled||review.missing||!review.anchor?'disabled':''}>Bestätigung lösen</button>`:`<button class="secondary" data-action="group-select" data-id="${group.id}" ${disabled||review.problem?'disabled':''}>Phasen auswählen</button><button data-action="group-confirm" data-id="${group.id}" ${disabled||review.problem?'disabled':''}>Als Cappuccino bestätigen</button>`}</div>
      ${review.problem&&(!review.manual||review.missing)?`<p class="muted edit-note">${CA_ESCAPE(review.problem)}</p>`:''}
    </article>`;
  }
  _cappuccinoHtml(){
    const f=this._data.features,groups=this._groups(),quantifier=f.cappuccino_match_mode==='at_least'?'mindestens':'genau';
    return `<section aria-label="Cappuccino-Gruppen"><div class="recipe-rule"><h3>Cappuccino verstehen</h3><p><strong>Aktuelles Rezept:</strong> 1 Espresso + ${quantifier} ${f.cappuccino_milk_phases} Milchphasen → ${f.cappuccino_cups} Tassen.${f.cappuccino_enabled?'':' Automatische Regel deaktiviert.'}</p><p class="muted">Milchphasen werden demselben Espresso zugeordnet, wenn das Profil gleich bleibt und die Pause zwischen den zugehörigen Phasen höchstens ${f.session_gap_s} s beträgt. Ein neuer Espresso startet eine neue Gruppe. Heizen, Spülen und unbekannte Phasen sind keine Rezeptzutaten. Die Regeln lassen sich in den Integrationsoptionen anpassen; eine manuelle Bestätigung hat Vorrang.</p></div>
    <div class="groups">${groups.map(group=>this._groupHtml(group)).join('')||'<p class="empty">Keine gespeicherten Espresso-/Milchgruppen für den gewählten Starttag.</p>'}</div>
    <p class="muted foot">Gruppen gehören zum lokalen Starttag in ${CA_ESCAPE(this._data.timezone)}; ihre Phasen können über Mitternacht reichen. Automatische Gruppen werden nach dem aktuellen Rezept bewertet. Das Detailprotokoll ist begrenzt: Tages- und Gesamtzähler können archivierte Zubereitungen enthalten, deren einzelne Phasen hier fehlen. Die sichtbaren Gruppen müssen deshalb nicht die Zählersumme ergeben. Milch und Tassenzahl sind eine Zuordnung, keine Volumenmessung.</p></section>`;
  }
  _learnHtml(){
    const p=this._data.proposal;
    const names={espresso_min_s:'Espresso mindestens (s)',espresso_max_s:'Espresso höchstens (s)',milk_pump_min_s:'Milchpumpe mindestens (s)',milk_pump_max_s:'Milchpumpe höchstens (s)',milk_heating_min_s:'Milch-Heizphase mindestens (s)',milk_heating_max_s:'Milch-Heizphase höchstens (s)'};
    const f=p.features,old=this._data.features;
    return `<section><h3>Aus deinen Bestätigungen lernen</h3><p class="muted">Lernbeispiele sind an Maschine und Profil gebunden. Einzelne Zuordnungen korrigieren sofort die Historie. Neue Grenzen werden als Vorschlag berechnet; gespeichert wird ein eigenes Profil.</p>
    <div class="stats">${['espresso','milk','heating','rinsing'].map(k=>`<div><strong>${p.counts[k]}</strong><span>${CA_LABELS[k]} bestätigt</span></div>`).join('')}</div>
    <div class="table"><table><thead><tr><th>Änderung</th><th>Bisher</th><th>Vorschlag</th></tr></thead><tbody>${p.changes.map(c=>`<tr><td>${names[c.key]||CA_ESCAPE(c.key)}</td><td>${c.before}</td><td>${c.after}</td></tr>`).join('')}
    ${['cappuccino_milk_phases','cappuccino_cups'].filter(k=>f[k]!==old[k]).map(k=>`<tr><td>${k==='cappuccino_cups'?'Cappuccino-Tassen pro Espresso':'Milchphasen pro Espresso'}</td><td>${old[k]}</td><td>${f[k]}</td></tr>`).join('')}
    ${!p.ready?'<tr><td colspan="3">Noch keine geänderten Grenzen oder Rezeptwerte.</td></tr>':''}</tbody></table></div>
    <p>${p.recipe_examples} bestätigte Cappuccino-Zubereitung(en) für dieses Profil.</p>${p.notes.map(n=>`<p class="muted">${CA_ESCAPE(n)}</p>`).join('')}
    <button data-action="profile" ${!p.ready||this._saving||this._data.busy?'disabled':''}>Vorschlag als Profil speichern</button></section>`;
  }
  _dayHtml(){
    const d=this._data,dates=[...new Set([...Object.keys(d.daily),...Object.keys(d.cappuccino_daily||{}),...d.events.map(e=>e.day),...(d.groups||[]).map(group=>this._localDay(this._groupSpan(group).started_at))])].filter(Boolean).sort().reverse();
    return `<div class="toolbar"><div><button data-action="day" data-day="today" class="${this._day==='today'?'chosen':'secondary'}">Heute</button><button data-action="day" data-day="yesterday" class="${this._day==='yesterday'?'chosen':'secondary'}">Gestern</button></div><label>Datum <select id="day"><option value="today" ${this._day==='today'?'selected':''}>Heute</option><option value="yesterday" ${this._day==='yesterday'?'selected':''}>Gestern</option>${dates.map(day=>`<option ${this._day===day?'selected':''}>${CA_ESCAPE(day)}</option>`).join('')}</select></label></div>`;
  }
  _statsHtml(){
    const d=this._data,counts=d.daily[this._date()]||{espresso:0,milchphasen:0};
    const capp=d.cappuccino_daily?.[this._date()]??(this._day==='today'?d.cappuccino_counts?.today:this._day==='yesterday'?d.cappuccino_counts?.yesterday:0);
    return `<div class="stats"><div><strong>${counts.espresso}</strong><span>Espresso</span></div><div><strong>${counts.milchphasen}</strong><span>Milchphasen</span></div><button type="button" class="stat-button" data-action="tab" data-tab="cappuccino" aria-label="Cappuccino-Gruppen und Erklärungen anzeigen"><strong>${capp??'–'}</strong><span>Cappuccino · Gruppen ansehen</span></button><div><strong>${d.cappuccino_total}</strong><span>Cappuccino insgesamt</span></div></div>`;
  }
  _phasesHtml(){
    const d=this._data,phases=this._events(),group=this._groupFocus!==null?this._group(this._groupFocus):null;
    const shown=phases.filter(e=>this._filter==='all'||e.classification===this._filter||this._filter==='unconfirmed'&&!e.reviewed_at&&!e.corrections?.length);
    const disabled=this._saving||d.busy;
    return `${group?`<div class="group-focus" role="status"><div><strong>Zugehörige Phasen der Gruppe</strong><p>${CA_ESCAPE(this._range(this._groupSpan(group)))} · Starttag ${CA_ESCAPE(this._localDay(this._groupSpan(group).started_at))}. Diese Ansicht enthält alle noch gespeicherten Gruppenphasen, auch am Folgetag. Der Kategorienfilter bleibt aktiv.</p></div><button class="secondary" data-action="clear-group">Alle Phasen am gewählten Tag</button></div>`:''}
    <p class="muted">Kategorie anklicken: passende Phasen anzeigen. Erneut anklicken: alle Phasen anzeigen. Zum Korrigieren eine Phase auf die Kategorie ziehen oder ihr Auswahlfeld verwenden; erst „Speichern“ übernimmt die Änderung. Cappuccino ist eine Gruppe aus Espresso und Milchphasen; die einzelnen Kategorien bleiben erhalten.</p>
    <div class="drops" aria-label="Kategorien filtern oder Phasen zuordnen">${['espresso','milk','heating','rinsing','unknown'].map(k=>`<button type="button" class="drop secondary ${k} ${this._filter===k?'active':''}" data-action="filter" data-filter="${k}" data-drop="${k}" aria-pressed="${this._filter===k}" title="${CA_LABELS[k]} anzeigen oder eine Phase zum Zuordnen hierhin ziehen"><ha-icon icon="mdi:${CA_ICONS[k]}"></ha-icon>${CA_LABELS[k]}</button>`).join('')}</div>
    <div class="toolbar"><label>Anzeige <select id="filter"><option value="all" ${this._filter==='all'?'selected':''}>Alle Phasen</option>${Object.entries(CA_LABELS).map(([k,v])=>`<option value="${k}" ${this._filter===k?'selected':''}>${v}</option>`).join('')}<option value="unconfirmed" ${this._filter==='unconfirmed'?'selected':''}>Noch unbestätigt</option></select></label><button data-action="recipe" ${this._selected.size<2||disabled?'disabled':''}>${this._selected.size} gewählt · Cappuccino zuordnen</button></div>
    <div class="phases">${shown.map(e=>this._phaseHtml(e)).join('')||'<p class="empty">Keine gespeicherten Phasen für diese Auswahl.</p>'}</div>
    <p class="muted foot">Zeiten in ${CA_ESCAPE(d.timezone)} · ${shown.length} von ${phases.length} gespeicherten Phasen ${group?'dieser Gruppe':'am gewählten Tag'} angezeigt. Aufbewahrung: höchstens ${d.retention.max_events} Phasen / ${d.retention.days} Tage. Älteste noch gespeicherte Phase: ${this._time(d.retention.oldest_start,true)}. Tageszähler können mehr Vorgänge enthalten als das begrenzte Detailprotokoll. Milchvolumen wird nicht gemessen.</p>`;
  }
  _render(){
    if(!this._config)return;
    const d=this._data;
    this.shadowRoot.innerHTML=`<style>${CoffeeReviewCard.styles}</style><ha-card><header><div><h2>${CA_ESCAPE(this._config.title||'Kaffee · Tagesauswertung')}</h2><p>${d?CA_ESCAPE(d.profile)+' · '+CA_ESCAPE(d.name):'Phasen verstehen und zuordnen'}</p></div><button class="secondary" data-action="refresh" ${this._loading||this._saving?'disabled':''}>↻ <span>Aktualisieren</span></button></header>
    ${this._detectors?.length>1?`<label>Maschine <select id="detector"><option value="" disabled ${!this._entry?'selected':''}>Maschine wählen</option>${this._detectors.map(x=>`<option value="${CA_ESCAPE(x.entry_id)}" ${x.entry_id===this._entry?'selected':''}>${CA_ESCAPE(x.name+' · '+x.power_entity)}</option>`).join('')}</select></label>`:''}
    ${this._error||this._actionError?`<p class="error" role="alert">${CA_ESCAPE(this._actionError||this._error)}</p>`:''}${this._message?`<p class="message" role="status">${CA_ESCAPE(this._message)}</p>`:''}
    ${d?`<div class="live"><span class="dot ${d.busy?'active':''}"></span>${CA_ESCAPE(d.status)}${d.phase_start?' · seit '+this._time(d.phase_start)+' · '+d.live_duration_s+' s':''}<span class="muted"> · ${CA_ESCAPE(d.quality.state)}</span></div>
    <nav aria-label="Auswertung"><button data-action="tab" data-tab="phases" aria-pressed="${this._tab==='phases'}" class="${this._tab==='phases'?'chosen':'secondary'}">Phasen & Zuordnung</button><button data-action="tab" data-tab="cappuccino" aria-pressed="${this._tab==='cappuccino'}" class="${this._tab==='cappuccino'?'chosen':'secondary'}">Cappuccino</button><button data-action="tab" data-tab="learn" aria-pressed="${this._tab==='learn'}" class="${this._tab==='learn'?'chosen':'secondary'}">Profil lernen</button></nav>
    ${this._confirmHtml()}
    ${this._tab==='learn'?this._learnHtml():this._dayHtml()+this._statsHtml()+(d.busy?'<p class="message">Eine Phase oder Aufnahme läuft. Korrekturen sind nach Abschluss wieder möglich.</p>':'')+(this._tab==='cappuccino'?this._cappuccinoHtml():this._phasesHtml())}`:'<p class="empty">'+(this._loading?'Auswertung wird geladen …':'Kaffee-Integration auswählen bzw. laden.')+'</p>'}</ha-card>`;
  }
}
CoffeeReviewCard.styles=`
:host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui);font-size:14px}*{box-sizing:border-box}ha-card{padding:20px;overflow:hidden}header,.row,.toolbar,.actions{display:flex;align-items:center;gap:12px;justify-content:space-between}header{margin-bottom:12px}h2{font-size:22px;margin:0}h3{font-size:18px}header p{margin:5px 0;color:var(--secondary-text-color)}button,select,input{font:inherit;color:var(--primary-text-color);border:1px solid var(--divider-color,#ddd);border-radius:10px;min-height:40px;background:var(--card-background-color,#fff);padding:8px 12px}button{cursor:pointer;background:var(--primary-color,#00796b);color:var(--text-primary-color,#fff);border-color:transparent;font-weight:600}button.secondary{background:transparent;color:var(--primary-text-color);border-color:var(--divider-color,#ddd)}button:disabled,select:disabled,input:disabled{opacity:.5;cursor:default}button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--primary-color);outline-offset:2px}.chosen{box-shadow:0 2px 6px #0002}nav{display:flex;gap:8px;border-bottom:1px solid var(--divider-color);padding-bottom:14px;margin:12px 0}label{display:flex;align-items:center;gap:8px}.toolbar{flex-wrap:wrap;margin:14px 0}.toolbar>div{display:flex;gap:6px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}.stats>div{background:var(--secondary-background-color,#f5f5f5);border-radius:12px;padding:14px}.stats strong{display:block;font-size:26px}.stats span{display:block;color:var(--secondary-text-color);font-size:12px;margin-top:4px}.live{padding:10px 12px;background:var(--secondary-background-color,#f5f5f5);border-radius:10px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#8d9696;margin-right:8px}.dot.active{background:#00a896}.muted{color:var(--secondary-text-color);font-size:13px;line-height:1.55}.drops{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.drop{border:1px dashed var(--divider-color,#ccc);border-radius:12px;padding:14px 8px;display:flex;gap:6px;align-items:center;justify-content:center;min-height:54px}.drop:hover{background:var(--secondary-background-color)}.drop.active{background:var(--secondary-background-color,#f5f5f5);border-color:var(--primary-color,#00796b);border-style:solid;box-shadow:0 0 0 1px var(--primary-color,#00796b)}ha-icon{--mdc-icon-size:22px;flex-shrink:0}.espresso ha-icon{color:#c48b24}.milk ha-icon{color:#00a5b8}.heating ha-icon{color:#cf7054}.unknown ha-icon,.unclear ha-icon{color:#83929a}.phases{max-height:min(55vh,520px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.phase{border:1px solid var(--divider-color,#ddd);border-left:4px solid #9ca9af;border-radius:12px;padding:14px;margin:10px 0}.phase.espresso{border-left-color:#c48b24}.phase.milk{border-left-color:#00a5b8}.phase.heating{border-left-color:#cf7054}.phase input[type=checkbox]{width:20px;height:20px;min-height:20px;padding:0;accent-color:var(--primary-color)}.title{flex:1;min-width:0}.time{font-variant-numeric:tabular-nums;line-height:1.6;font-size:13px}.time span{color:var(--secondary-text-color)}.reason{color:var(--secondary-text-color);margin:8px 0 8px 44px;font-size:13px;line-height:1.5}summary{cursor:pointer;padding:8px 0;color:var(--primary-color);font-weight:600}details p{margin:8px 0}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--divider-color,#ddd);vertical-align:top}th{color:var(--secondary-text-color)}.table{overflow-x:auto}.pass{color:#009a78}.fail{color:var(--secondary-text-color)}.confirm{padding:16px;background:var(--secondary-background-color,#f5f5f5);border:2px solid var(--primary-color);border-radius:12px;margin:16px 0;line-height:1.55}.confirm input{max-width:100%}.confirm label{margin:12px 0}.actions{justify-content:flex-start;margin-top:12px}.error{background:#b62c2c18;color:var(--error-color,#b62c2c);padding:12px;border-radius:10px}.message{background:#009a7815;padding:12px;border-radius:10px;line-height:1.5}.empty{text-align:center;padding:24px;color:var(--secondary-text-color)}.drink{background:#a67c5214;border-radius:8px;padding:8px 12px}.link{background:none;color:var(--primary-color);padding:4px 8px;font-size:12px}.audit{font-size:12px;color:var(--secondary-text-color);padding:4px 0}.foot{border-top:1px solid var(--divider-color);padding-top:12px}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
nav{flex-wrap:wrap}.stats>button.stat-button{background:var(--secondary-background-color,#f5f5f5);border:1px solid transparent;border-radius:12px;padding:14px;text-align:left;color:var(--primary-text-color);font-weight:400}.stats>button.stat-button:hover{border-color:var(--primary-color)}.recipe-rule{border:1px solid var(--divider-color,#ddd);border-radius:12px;padding:0 16px 6px;margin:14px 0;line-height:1.55}.recipe-rule h3{margin-bottom:8px}.groups{max-height:min(65vh,760px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.recipe-group{border:1px solid var(--divider-color,#ddd);border-left:4px solid #9ca9af;border-radius:12px;padding:16px;margin:12px 0}.recipe-group.matched{border-left-color:#009a78}.recipe-group.pending{border-left-color:#c48b24}.group-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.group-head>div{min-width:0}.group-head strong{font-size:16px}.recipe-status{background:var(--secondary-background-color,#f5f5f5);border-radius:8px;padding:6px 9px;line-height:1.4;font-size:12px;font-weight:600;flex-shrink:0;max-width:48%}.recipe-status.matched{background:#009a7818}.recipe-status.pending{background:#c48b2418}.group-counts{margin:10px 0;color:var(--secondary-text-color);font-size:13px;line-height:1.5}.group-counts span{padding:0 4px}.recipe-reasons{line-height:1.55;font-size:13px}.recipe-reasons p{margin:8px 0}.recipe-group h4{font-size:13px;margin:16px 0 8px}.group-phases{list-style:none;margin:0;padding:0}.group-phases li{display:flex;gap:10px;align-items:center;padding:7px 0;border-bottom:1px solid var(--divider-color,#ddd)}.group-phases span{display:block;color:var(--secondary-text-color);font-size:12px;line-height:1.5}.group-phases strong{font-size:13px}.group-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.group-actions button{font-size:13px}.edit-note{margin-bottom:0}.group-focus{display:flex;align-items:center;justify-content:space-between;gap:12px;border:1px solid var(--primary-color);border-radius:10px;padding:12px;margin:14px 0;line-height:1.5}.group-focus p{font-size:13px;color:var(--secondary-text-color);margin:5px 0 0}.group-focus button{flex-shrink:0;font-size:13px}.recipe-rule p,.recipe-reasons p,.group-head strong,.group-counts{overflow-wrap:anywhere}
@media(max-width:650px){ha-card{padding:14px}h2{font-size:19px}.stats{grid-template-columns:repeat(2,1fr)}.drops{grid-template-columns:repeat(2,1fr)}.row{display:grid;grid-template-columns:20px 24px minmax(0,1fr);gap:8px}.row select{grid-column:1/-1;margin-left:0;width:100%}.time span{display:block}.reason{margin-left:0}header button span{display:none}nav button{font-size:12px}.recipe-group{padding:12px}.group-head{flex-direction:column;gap:8px}.recipe-status{max-width:100%}.group-actions button{flex:1 1 140px}.group-focus{align-items:stretch;flex-direction:column}.confirm label{align-items:flex-start;flex-direction:column}th,td{padding:8px 5px}.toolbar button{font-size:12px}}
`;
if(!customElements.get('coffee-review-card'))customElements.define('coffee-review-card',CoffeeReviewCard);
window.customCards=window.customCards||[];
if(!window.customCards.some(c=>c.type==='coffee-review-card'))window.customCards.push({type:'coffee-review-card',name:'Kaffee · Tagesauswertung',description:'Phasen mit Zeitfenstern, Zuordnung und Profilvorschlägen',preview:false});
}).catch(error => console.error('Kaffee-Auswertung konnte nicht registriert werden.', error));
