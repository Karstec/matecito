import { $ } from "./core/dom.js";
import { MEDIOS_NORM } from "./core/process-metadata.js";
import { estado } from "./core/state.js";
import {
  actualizarLimiteOsint,
  initOsint,
  LIMITE_INTERACCIONES_OSINT,
  limiteInteraccionesOsint,
} from "./features/osint.js";
import { initPadronSearch } from "./features/padron-search.js";
import {
  arrancarSeguimiento,
  detenerPoll,
  initProcessRunner,
} from "./features/process-runner.js";
import { cargarHistorial, initHistory } from "./features/history.js";
import {
  initNavigation,
  irInicio,
  ocultarTodo,
  reiniciarWizard,
} from "./features/navigation.js";

/* ---------- inicio ---------- */
async function init(){
  const r = await fetch("/api/estado").then(x=>x.json());
  estado.usuario = r.usuario || "";
  estado.agenteMails = r.agente_mails;
  if(!r.agente_mails){
    const av = document.createElement("div");
    av.className = "aviso aviso-rojo";
    av.style.margin = "0 0 16px";
    av.innerHTML = `⚠ <b>El agente de mails no está disponible.</b> ${r.agente_mails_error||""}
      <br>La validación de teléfonos funciona igual. Corregí la ubicación de jueves.py y reiniciá el servidor.`;
    document.querySelector(".contenido").prepend(av);
  }
  if(!estado.usuario){ cambiarUsuario(true); }
  $("nombreUsuario").textContent = estado.usuario || "USUARIO";
  $("fUsuarioTabla").value = estado.usuario;
  for(const sel of [$("selPais"), $("selPaisArch")]){
    sel.innerHTML = "";
    for(const [cod, nom] of Object.entries(r.paises_telefono))
      sel.add(new Option(`${nom} (${cod})`, cod));
  }
  cargarPresets();
  actualizarPreview();
  irInicio();
}
async function cambiarUsuario(forzado){
  const u = prompt("Nombre de usuario (se usa para nombrar las tablas de resultados):", estado.usuario || "");
  if(u === null && !forzado) return;
  const val = (u||"").trim();
  if(!val){ if(forzado) return cambiarUsuario(true); return; }
  await fetch("/api/usuario",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({usuario:val})});
  estado.usuario = val;
  $("nombreUsuario").textContent = val;
  $("fUsuarioTabla").value = val;
  actualizarPreview();
}
$("nombreUsuario").onclick = ()=>cambiarUsuario(false);

/* ---------- paso 1: origen ---------- */
function elegirOrigen(o){
  estado.origen = o;
  $("opDB").classList.toggle("sel", o==="db");
  $("opArchivo").classList.toggle("sel", o==="archivo");
  $("cartaConexion").classList.toggle("oculto", o!=="db");
  $("cartaArchivo").classList.toggle("oculto", o!=="archivo");
  $("cartaSeleccion").classList.add("oculto");
  $("cartaGeneracion").classList.add("oculto");
  $("cartaProgreso").classList.add("oculto");
  if(estado.proceso==="osint"){
    const destino = o==="db" ? $("cartaGeneracion") : $("cartaArchivo");
    destino.insertBefore($("grupoOsint"), destino.querySelector(".fila-botones"));
    $("grupoOsint").classList.remove("oculto");
  }
}
$("opDB").onclick = ()=>elegirOrigen("db");
$("opArchivo").onclick = ()=>elegirOrigen("archivo");
for(const op of [$("opDB"), $("opArchivo")])
  op.onkeydown = e=>{ if(e.key==="Enter"||e.key===" ") op.click(); };

/* ---------- presets ---------- */
async function cargarPresets(){
  const p = await fetch("/api/presets").then(x=>x.json());
  const sel = $("selPreset");
  sel.innerHTML = '<option value="">— Personalizado —</option>';
  Object.keys(p).forEach(n=>sel.add(new Option(n,n)));
  sel.dataset.presets = JSON.stringify(p);
}
$("selPreset").onchange = ()=>{
  const p = JSON.parse($("selPreset").dataset.presets||"{}")[$("selPreset").value];
  if(!p) return;
  $("fDbType").value = p.db_type||"oracle"; $("fHost").value = p.host||"";
  $("fPort").value = p.port||""; $("fUser").value = p.user||"";
  $("fDbname").value = p.dbname||"";
};
$("btnGuardarPreset").onclick = async ()=>{
  const nombre = prompt("Nombre del preset (ej: Mar del Plata):");
  if(!nombre) return;
  await fetch("/api/presets",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({nombre, datos:{
      db_type:$("fDbType").value, host:$("fHost").value, port:$("fPort").value,
      user:$("fUser").value, dbname:$("fDbname").value}})});
  await cargarPresets();
  $("selPreset").value = nombre;
  $("msgConexion").textContent = `Preset "${nombre}" guardado (sin contraseña).`;
};

/* ---------- paso 2: conectar ---------- */
$("btnConectar").onclick = async ()=>{
  $("msgConexion").textContent = "Conectando…"; $("btnConectar").disabled = true;
  try{
    const r = await fetch("/api/conexion",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({db_type:$("fDbType").value, host:$("fHost").value, port:$("fPort").value,
        user:$("fUser").value, password:$("fPass").value, dbname:$("fDbname").value,
        })});
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail||"Error de conexión");
    estado.session = j.session_id;
    $("msgConexion").innerHTML = '<span style="color:var(--verde-osc)">✔ Conectado</span>';
    $("chipEstado").textContent = "conectado";

    const sel = $("selEsquema");
    sel.innerHTML = '<option value="">Elegir…</option>';
    j.esquemas.forEach(e=>sel.add(new Option(e,e)));
    $("cartaSeleccion").classList.remove("oculto");
    const esDenom = estado.proceso==="denominacion" || estado.proceso==="comparacion";
    $("preguntaTabla").classList.toggle("oculto", !esDenom);
    $("gridSeleccion").classList.toggle("oculto", esDenom);
    $("opMismaTabla").classList.remove("sel"); $("opTablasDistintas").classList.remove("sel");
    $("avisoTablasDistintas").classList.add("oculto");
  }catch(e){
    $("msgConexion").innerHTML = `<span style="color:var(--rojo)">✖ ${e.message}</span>`;
  }finally{ $("btnConectar").disabled = false; }
};

/* ---------- pregunta misma tabla (denominación) ---------- */
$("opMismaTabla").onclick = ()=>{
  $("opMismaTabla").classList.add("sel"); $("opTablasDistintas").classList.remove("sel");
  $("avisoTablasDistintas").classList.add("oculto");
  $("gridSeleccion").classList.remove("oculto");
};
$("opTablasDistintas").onclick = ()=>{
  $("opTablasDistintas").classList.add("sel"); $("opMismaTabla").classList.remove("sel");
  $("avisoTablasDistintas").classList.remove("oculto");
  $("gridSeleccion").classList.add("oculto");
  $("cartaGeneracion").classList.add("oculto");
};
for(const op of [$("opMismaTabla"), $("opTablasDistintas")])
  op.onkeydown = e=>{ if(e.key==="Enter"||e.key===" ") op.click(); };

/* ---------- paso 3: esquema → tabla → columnas ---------- */
$("selEsquema").onchange = async ()=>{
  const esq = $("selEsquema").value;
  const selT = $("selTabla");
  selT.innerHTML = '<option value="">Elegir…</option>'; selT.disabled = !esq;
  $("selColId").disabled = $("selColDato").disabled = $("btnVerColumnas").disabled = true;
  $("cartaGeneracion").classList.add("oculto");
  if(!esq) return;
  const j = await fetch(`/api/conexion/${estado.session}/tablas?esquema=${encodeURIComponent(esq)}`).then(x=>x.json());
  (j.tablas||[]).forEach(t=>selT.add(new Option(t,t)));
};
$("selTabla").onchange = async ()=>{
  const esq = $("selEsquema").value, tab = $("selTabla").value;
  const s1 = $("selColId"), s2 = $("selColDato");
  s1.innerHTML = s2.innerHTML = '<option value="">Elegir…</option>';
  s1.disabled = s2.disabled = $("btnVerColumnas").disabled = !tab;
  $("cartaGeneracion").classList.add("oculto");
  if(!tab) return;
  const j = await fetch(`/api/conexion/${estado.session}/columnas?esquema=${encodeURIComponent(esq)}&tabla=${encodeURIComponent(tab)}`).then(x=>x.json());
  estado.columnas = j.columnas||[];
  estado.columnas.forEach(c=>{ s1.add(new Option(c.nombre,c.nombre)); s2.add(new Option(c.nombre,c.nombre)); });
};
function chequearColumnas(){
  // Cuitificación usa UNA sola columna (el número): la denominación no se
  // aporta, se TRAE del padrón. El resto de los procesos necesita las dos.
  const soloUna = estado.proceso==="cuitificacion";
  const listo = soloUna ? !!$("selColId").value
                        : ($("selColId").value && $("selColDato").value);
  if(listo){
    $("cartaGeneracion").classList.remove("oculto");
    actualizarPreview();
  }
}
$("selColId").onchange = chequearColumnas;
$("selColDato").onchange = chequearColumnas;

$("btnVerColumnas").onclick = ()=>{
  const tb = $("tablaColumnas").querySelector("tbody");
  tb.innerHTML = "";
  (estado.columnas||[]).forEach(c=>{
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${c.nombre}</td><td>${c.tipo||""}</td><td>${c.largo??""}</td>`;
    tb.appendChild(tr);
  });
  $("veloColumnas").classList.remove("oculto");
};
$("btnCerrarColumnas").onclick = ()=>$("veloColumnas").classList.add("oculto");

/* ---------- paso 4: preview + iniciar ---------- */
function sanitizar(t){ return (t||"").normalize("NFKD").replace(/[\u0300-\u036f]/g,"")
  .replace(/[^A-Za-z0-9]+/g,"_").replace(/^_+|_+$/g,"").toUpperCase(); }
function ahoraTs(){
  const d = new Date(), p = n=>String(n).padStart(2,"0");
  return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
function actualizarPreview(){
  const u = sanitizar(estado.usuario)||"USUARIO", c = sanitizar($("fCliente").value);
  $("previewTabla").textContent = c ? `${u}_${c}_${ahoraTs()}` : `${u}_${ahoraTs()}`;
  $("avisoCliente").classList.toggle("oculto", !!c);
}
$("fCliente").oninput = actualizarPreview;

$("btnIniciarDB").onclick = ()=>{
  if(!$("fCliente").value.trim()){
    $("previewSinCliente").textContent = `${sanitizar(estado.usuario)||"USUARIO"}_${ahoraTs()}`;
    $("veloConfirma").classList.remove("oculto");
    return;
  }
  iniciarProcesoDB();
};
$("btnConfirmarSin").onclick = ()=>{ $("veloConfirma").classList.add("oculto"); iniciarProcesoDB(); };
$("btnCancelarSin").onclick = ()=>{ $("veloConfirma").classList.add("oculto"); $("fCliente").focus(); };


/* ---------- previsualización de la tabla origen ----------
   Confirma, antes de ejecutar, que la tabla y las columnas elegidas contienen
   lo que uno cree. Elegir MAIL cuando la buena era MAIL_ALTERNATIVO, o una
   columna vacía en el 90% de las filas, sin esto se descubre DESPUÉS de correr
   el proceso.

   Usa el MISMO render que el cruce de denominaciones (tablaMini). Tener dos
   formatos de previsualización obliga a quien la mira a reaprender la pantalla
   según por dónde entró, y a nosotros a arreglar cada cosa dos veces.

   Es de solo lectura y no bloquea: el proceso se puede ejecutar sin abrirla.
   La confirmación la da la persona, no este código. */
function prevColumnasElegidas(){
  // Solo las columnas que el proceso va a usar. Si todavía no hay ninguna
  // elegida se muestran todas: ver la tabla entera también sirve para decidir.
  const c = [];
  ["selColId","selColDato"].forEach(id=>{
    const el = $(id);
    if(el && el.value && !el.closest(".oculto")) c.push(el.value);
  });
  return [...new Set(c)];
}

async function cargarPrevisualizacion(){
  const esq = $("selEsquema").value, tab = $("selTabla").value;
  if(!estado.session || !tab){
    $("prevCuerpo").innerHTML =
      "<div class='pasoinfo'>Elegí una tabla primero.</div>";
    return;
  }
  $("prevCuerpo").innerHTML = "<div class='pasoinfo'>Leyendo primeros registros…</div>";
  const cols = prevColumnasElegidas();
  const q = `esquema=${encodeURIComponent(esq)}&tabla=${encodeURIComponent(tab)}`
          + `&limite=10&columnas=${encodeURIComponent(cols.join(","))}`;
  try{
    const r = await fetch(`/api/conexion/${estado.session}/muestra?${q}`);
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "no se pudo leer");
    const alcance = cols.length ? cols.join(", ") : "todas las columnas";
    tablaMini("prevCuerpo", d.columnas, d.filas,
      `${esq ? esq+"." : ""}${tab} — primeras ${d.cantidad} filas · ${alcance}`);
    avisarColumnasNulas("prevCuerpo", d);
  }catch(e){
    $("prevCuerpo").innerHTML = `<div style="color:#c05;font-size:12px">Error: ${e.message}</div>`;
  }
}

$("btnPrevisualizar").onclick = cargarPrevisualizacion;

// Si cambia la tabla o alguna columna con la muestra ya cargada, se recarga
// sola: una muestra vieja de otra tabla es peor que no mostrar nada.
["selEsquema","selTabla","selColId","selColDato"].forEach(id=>{
  const el = $(id);
  if(!el) return;
  el.addEventListener("change", ()=>{
    if($("prevCuerpo").innerHTML.trim()) cargarPrevisualizacion();
  });
});

/* ---------- previsualización del archivo plano ----------
   El flujo por archivo solo leía el contenido al ejecutar, así que un
   encabezado mal detectado o una columna equivocada se descubrían con el
   proceso ya corrido. El archivo se manda, se lee en memoria y se descarta:
   no queda en disco para mostrar 10 filas.

   Mismo render que las otras dos previsualizaciones (tablaMini). */
async function previsualizarArchivo(idInput, idCuerpo){
  const f = $(idInput).files[0];
  if(!f){
    $(idCuerpo).innerHTML = "<div class='pasoinfo'>Elegí un archivo primero.</div>";
    return;
  }
  $(idCuerpo).innerHTML = "<div class='pasoinfo'>Leyendo…</div>";
  const fd = new FormData();
  fd.append("archivo", f);
  fd.append("limite", 10);
  try{
    const r = await fetch("/api/archivo/muestra", {method:"POST", body:fd});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "no se pudo leer");
    if(!d.cantidad){
      $(idCuerpo).innerHTML = "<div style='color:#c05;font-size:12px'>El archivo no tiene filas.</div>";
      return;
    }
    tablaMini(idCuerpo, d.columnas, d.filas,
      `${f.name} — primeras ${d.cantidad} de ${d.total} filas`);
    avisarColumnasNulas(idCuerpo, d);
  }catch(e){
    $(idCuerpo).innerHTML = `<div style="color:#c05;font-size:12px">Error: ${e.message}</div>`;
  }
}

$("btnPrevArch").onclick  = ()=>previsualizarArchivo("fArchivo", "prevArchCuerpo");
$("btnPrevArchN").onclick = ()=>previsualizarArchivo("fArchivoN", "prevArchCuerpoN");

// Si se cambia el archivo con una muestra ya cargada, se limpia: mostrar las
// filas de un archivo que ya no es el elegido es peor que no mostrar nada.
[["fArchivo","prevArchCuerpo"],["fArchivoN","prevArchCuerpoN"]].forEach(([i,c])=>{
  const el = $(i);
  if(el) el.addEventListener("change", ()=>{ $(c).innerHTML = ""; });
});

/* ---------- cruce de nombres contra la base ----------
   Panel autocontenido de tres pasos: archivo -> base -> confirmar.

   No asume que ya hay una conexión abierta. Pide las credenciales acá mismo
   porque la base contra la que se compara NO es el padrón BCRA: es cualquier
   padrón del cliente, y cambia de una corrida a la otra. Si además hay una
   conexión abierta en la pantalla principal, se puede reusar con un botón.

   Cada paso se habilita cuando el anterior está resuelto. Un paso bloqueado
   se ve grisado y no responde: evita configurar el lado base contra un
   archivo que todavía no se leyó. */
const cruce = {token:null, columnas:[], sid:null, muestraOk:false};

function opciones(sel, valores, permitirVacio){
  const s = $(sel); if(!s) return;
  s.innerHTML = "";
  if(permitirVacio) s.appendChild(new Option("(ninguna)",""));
  valores.forEach(v=>s.appendChild(new Option(v,v)));
}
function elegirPorNombre(sel, candidatos){
  const s = $(sel); if(!s) return;
  for(const c of candidatos)
    for(const o of s.options)
      if(o.value.toUpperCase().includes(c)){ s.value = o.value; return; }
}
function pasoActivo(id, activo){
  $(id).classList.toggle("bloqueado", !activo);
}
function avisarColumnasNulas(cont, d){
  // Una columna que viene TODA nula en la muestra casi seguro no es la que se
  // busca. Es una señal sobre las 10 filas, no una estadística de la tabla.
  const nulas = (d.diagnostico || []).filter(x=>x.nulos === d.cantidad).map(x=>x.columna);
  if(!nulas.length) return;
  $(cont).innerHTML +=
    `<div style="color:#c05;font-size:12px;margin-top:6px">⚠ Vienen todas nulas: `
    + `<b>${nulas.join(", ")}</b>. Revisá si es la columna correcta.</div>`;
}

function tablaMini(cont, columnas, filas, titulo){
  if(!filas.length){ $(cont).innerHTML =
    `<div class="minicab">${titulo}</div><div style="color:#c05;font-size:12px">Sin filas.</div>`; return; }
  let h = `<div class="minicab">${titulo}</div><div class="miniwrap"><table class="minitabla"><thead><tr>`;
  h += columnas.map(c=>`<th>${String(c).replace(/</g,"&lt;")}</th>`).join("") + "</tr></thead><tbody>";
  filas.forEach(f=>{
    h += "<tr>" + f.map(v=>{
      if(v === null || v === undefined) return "<td class='nulo'>(null)</td>";
      if(String(v) === "") return "<td class='nulo'>(vacío)</td>";
      return `<td>${String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;")}</td>`;
    }).join("") + "</tr>";
  });
  $(cont).innerHTML = h + "</tbody></table></div>";
}

$("btnAbrirCruce").onclick = ()=>{
  // Carta inline, igual que el resto de los procesos: hereda el ancho, el
  // responsive y el scroll del contenedor en vez de vivir en un modal fijo.
  detenerPoll();
  ocultarTodo();
  document.querySelectorAll(".navbtn").forEach(x=>x.classList.remove("activo"));
  $("btnAbrirCruce").classList.add("activo");
  $("tituloProceso").textContent = "Cruce de denominaciones";
  $("chipEstado").textContent = "configurando";
  $("cartaCruce").classList.remove("oculto");
  $("cruceAviso").textContent = "";
  // Si ya hay una conexión abierta se ofrece reusarla, pero no se asume.
  $("btnCruceUsarActual").style.display = estado.session ? "" : "none";
  revisarCruceListo();
};

/* --- PASO 1: archivo --- */
$("btnCruceSubir").onclick = async ()=>{
  const f = $("cruceArchivo").files[0];
  if(!f){ $("cruceInfoArchivo").textContent = "Elegí un archivo primero."; return; }
  $("cruceInfoArchivo").textContent = "Leyendo…";
  const fd = new FormData(); fd.append("archivo", f);
  try{
    const r = await fetch("/api/cruce-redes/subir", {method:"POST", body:fd});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "no se pudo leer");
    cruce.token = d.token; cruce.columnas = d.columnas; cruce.muestraOk = false;
    $("cruceInfoArchivo").textContent =
      `${d.archivo} · ${d.filas} filas · ${d.columnas.length} columnas`;
    ["cruceColNombre","cruceColId"].forEach(id=>opciones(id, d.columnas, false));
    ["cruceColUser","cruceColTel","cruceColMail"].forEach(id=>opciones(id, d.columnas, true));
    elegirPorNombre("cruceColNombre", ["NOMBRE","NAME","DENOMIN"]);
    elegirPorNombre("cruceColId", ["N","ID"]);
    elegirPorNombre("cruceColUser", ["USERNAME","USUARIO","PERFIL"]);
    elegirPorNombre("cruceColTel", ["TELEFONO","TEL","PHONE","CELULAR"]);
    elegirPorNombre("cruceColMail", ["EMAIL","MAIL","CORREO"]);
    $("cruceBloqueCols").classList.remove("oculto");
    pasoActivo("pasoBase", true);
  }catch(e){
    $("cruceInfoArchivo").innerHTML = `<span style="color:#c05">Error: ${e.message}</span>`;
    cruce.token = null; pasoActivo("pasoBase", false);
  }
  revisarCruceListo();
};

/* --- PASO 2: base --- */
async function tomarSesion(sid, etiqueta){
  cruce.sid = sid; cruce.muestraOk = false;
  $("cruceMsgConexion").innerHTML = `<span style="color:var(--verde-osc)">✔ ${etiqueta}</span>`;
  $("cruceBloqueSeleccion").classList.remove("oculto");
  const r = await fetch(`/api/conexion/${sid}/tablas?esquema=`);
  await cargarEsquemasCruce();
  pasoActivo("pasoPrev", true);
  revisarCruceListo();
}

$("btnCruceConectar").onclick = async ()=>{
  $("cruceMsgConexion").textContent = "Conectando…";
  try{
    const r = await fetch("/api/conexion", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({db_type:$("cxDbType").value, host:$("cxHost").value,
        port:$("cxPort").value, user:$("cxUser").value,
        password:$("cxPass").value, dbname:$("cxDbname").value})});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "no se pudo conectar");
    opciones("cruceEsquema", d.esquemas || [], false);
    // La contraseña no queda en el DOM más de lo necesario: la sesión ya
    // está abierta del lado del servidor y el campo no se vuelve a usar.
    $("cxPass").value = "";
    await tomarSesion(d.session_id, "Conectado");
  }catch(e){
    $("cruceMsgConexion").innerHTML = `<span style="color:#c05">${e.message}</span>`;
    cruce.sid = null; pasoActivo("pasoPrev", false); revisarCruceListo();
  }
};

$("btnCruceUsarActual").onclick = ()=>{
  if(!estado.session) return;
  const origen = $("selEsquema");
  opciones("cruceEsquema", [...origen.options].map(o=>o.value).filter(v=>v), false);
  if(origen.value) $("cruceEsquema").value = origen.value;
  tomarSesion(estado.session, "Usando la conexión de la pantalla principal");
};

async function cargarEsquemasCruce(){
  if(!$("cruceEsquema").options.length && cruce.sid){
    const r = await fetch(`/api/conexion/${cruce.sid}/tablas?esquema=`);
  }
  await cargarTablasCruce();
}
async function cargarTablasCruce(){
  const esq = $("cruceEsquema").value;
  if(!esq || !cruce.sid) return;
  const r = await fetch(`/api/conexion/${cruce.sid}/tablas?esquema=${encodeURIComponent(esq)}`);
  const d = await r.json();
  opciones("cruceTabla", d.tablas || [], false);
  await cargarColumnasCruce();
}
async function cargarColumnasCruce(){
  const esq = $("cruceEsquema").value, tab = $("cruceTabla").value;
  if(!esq || !tab || !cruce.sid) return;
  const r = await fetch(`/api/conexion/${cruce.sid}/columnas?esquema=${encodeURIComponent(esq)}&tabla=${encodeURIComponent(tab)}`);
  const d = await r.json();
  const cols = (d.columnas || []).map(c => typeof c === "string" ? c : (c.nombre || c[0]));
  opciones("cruceColIdBase", cols, false);
  opciones("cruceColNombreBase", cols, false);
  opciones("cruceColDoc", cols, true);
  elegirPorNombre("cruceColIdBase", ["CONCOD","ID","COD"]);
  elegirPorNombre("cruceColNombreBase", ["NOMCOMPLETO","NOMBRE","DENOMIN"]);
  elegirPorNombre("cruceColDoc", ["DOC","DNI","RUT","CUIT","CI"]);
  cruce.muestraOk = false;
  revisarCruceListo();
}
$("cruceEsquema").onchange = cargarTablasCruce;
$("cruceTabla").onchange = cargarColumnasCruce;
["cruceColNombreBase","cruceColIdBase","cruceColDoc"].forEach(id=>{
  $(id).addEventListener("change", ()=>{ cruce.muestraOk = false; revisarCruceListo(); });
});

/* --- PASO 3: confirmar --- */
$("btnCrucePrev").onclick = async ()=>{
  $("crucePrevBase").innerHTML = "Leyendo…";
  // Lado archivo: sale de las columnas ya leídas, sin volver al servidor.
  try{
    const r = await fetch(`/api/cruce-redes/columnas-archivo?ruta=${encodeURIComponent(cruce.token)}`);
    const d = await r.json();
    if(r.ok && d.muestra)
      tablaMini("crucePrevArchivo", d.columnas, d.muestra, "Archivo — primeras filas");
    else
      $("crucePrevArchivo").innerHTML =
        `<div class="minicab">Archivo</div><div class="pasoinfo">${cruce.columnas.join(" · ")}</div>`;
  }catch(e){ $("crucePrevArchivo").innerHTML = ""; }

  // Lado base: solo las columnas que el proceso va a usar.
  const cols = [$("cruceColIdBase").value, $("cruceColNombreBase").value,
                $("cruceColDoc").value].filter(Boolean);
  const q = `esquema=${encodeURIComponent($("cruceEsquema").value)}`
          + `&tabla=${encodeURIComponent($("cruceTabla").value)}`
          + `&limite=10&columnas=${encodeURIComponent(cols.join(","))}`;
  try{
    const r = await fetch(`/api/conexion/${cruce.sid}/muestra?${q}`);
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "no se pudo leer");
    tablaMini("crucePrevBase", d.columnas, d.filas,
              `Base — ${$("cruceTabla").value}, primeras ${d.cantidad} filas`);
    avisarColumnasNulas("crucePrevBase", d);
    cruce.muestraOk = true;
    revisarCruceListo();
  }catch(e){
    $("crucePrevBase").innerHTML = `<span style="color:#c05">Error: ${e.message}</span>`;
  }
};

function revisarCruceListo(){
  const ok = !!cruce.token && !!cruce.sid && !!$("cruceTabla").value &&
             !!$("cruceColNombreBase").value && cruce.muestraOk;
  $("btnCruceEjecutar").disabled = !ok;
  $("btnCruceEjecutar").title = ok ? "" :
    "Completá los 3 pasos: archivo, base y confirmación de las primeras filas.";
}

$("btnCruceEjecutar").onclick = async ()=>{
  const idBase = $("cruceColIdBase").value, nomBase = $("cruceColNombreBase").value;
  if(idBase === nomBase){
    $("cruceAviso").innerHTML = "<span style='color:#c05'>El identificador y el nombre no pueden ser la misma columna.</span>";
    return;
  }
  const extra = [$("cruceColUser").value, $("cruceColTel").value, $("cruceColMail").value].filter(Boolean);
  const fd = new FormData();
  fd.append("session_id", cruce.sid);
  fd.append("token", cruce.token);
  fd.append("origen", "archivo");
  fd.append("esquema", $("cruceEsquema").value);
  fd.append("tabla_base", $("cruceTabla").value);
  fd.append("col_id_base", idBase);
  fd.append("col_denom_base", nomBase);
  fd.append("col_doc_base", $("cruceColDoc").value);
  fd.append("col_denom_archivo", $("cruceColNombre").value);
  fd.append("col_id_archivo", $("cruceColId").value);
  fd.append("col_usuario", $("cruceColUser").value);
  fd.append("col_telefono", $("cruceColTel").value);
  fd.append("col_mail", $("cruceColMail").value);
  fd.append("where_base", $("cruceWhere").value);
  fd.append("candidatos_por_fila", $("cruceCandidatos").value);
  fd.append("usuario", (estado.usuario || "MATECITO"));
  fd.append("cliente", ($("cruceTabla").value || "CRUCE"));

  $("btnCruceEjecutar").disabled = true;
  $("cruceAviso").textContent = "Lanzando el cruce…";
  try{
    const r = await fetch("/api/cruce-redes/ejecutar", {method:"POST", body:fd});
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "falló el cruce");
    // El temporal se borra en el servidor al terminar: otra corrida necesita
    // volver a subir el archivo.
    cruce.token = null; cruce.muestraOk = false;
    ocultarTodo();
    arrancarSeguimiento(d.job_id);
  }catch(e){
    $("cruceAviso").innerHTML = `<span style="color:#c05">Error: ${e.message}</span>`;
    revisarCruceListo();
  }
};


/* ---------- spinner de umbral (0-100, solo con flechas) ----------
   El input es readonly a propósito: el porcentaje NO se tipea libre, se
   sube y se baja con las flechas (ambas del mismo color). Se mantiene
   como <input type=number> para que el teclado (↑/↓) también funcione. */
function ajustarSpin(idInput, paso){
  const el = $(idInput);
  let v = parseInt(el.value, 10);
  if(isNaN(v)) v = 80;
  v = Math.min(100, Math.max(0, v + paso));
  el.value = v;
  if(idInput === "fUmbral") $("txtUmbral").textContent = v;
}
document.querySelectorAll(".spin-btn").forEach(b=>{
  let timer=null, repite=null;
  const uno = ()=>ajustarSpin(b.dataset.target, parseInt(b.dataset.paso,10));
  b.onclick = uno;
  // mantener apretado = repetir (subir/bajar de a poco, cómodo hasta 100)
  b.onmousedown = ()=>{ timer = setTimeout(()=>{ repite = setInterval(uno, 70); }, 400); };
  const soltar = ()=>{ clearTimeout(timer); clearInterval(repite); };
  b.onmouseup = soltar; b.onmouseleave = soltar;
});
for(const id of ["fUmbral","fUmbralArch"]){
  $(id).onkeydown = e=>{
    if(e.key==="ArrowUp"){ e.preventDefault(); ajustarSpin(id, 1); }
    if(e.key==="ArrowDown"){ e.preventDefault(); ajustarSpin(id, -1); }
  };
}

function umbralElegido(idInput){
  const v = parseInt($(idInput).value, 10);
  return isNaN(v) ? 80 : Math.min(100, Math.max(0, v));
}

async function iniciarProcesoDB(){
  estado.esArchivo = false;
  const proveedores = [...document.querySelectorAll('input[name="proveedorOsint"]:checked')]
    .map(el=>el.value);
  if(estado.proceso==="osint" && proveedores.length===0){
    alert("Elegí al menos un proveedor OSINT.");
    return;
  }
  if(estado.proceso==="osint" && !actualizarLimiteOsint()){
    alert(`El máximo permitido es ${LIMITE_INTERACCIONES_OSINT.toLocaleString("es-AR")} interacciones OSINT por ejecución.`);
    return;
  }
  const r = await fetch("/api/procesos/db",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({session_id:estado.session, proceso:estado.proceso,
      esquema:$("selEsquema").value, tabla:$("selTabla").value,
      col_id:$("selColId").value, col_dato:$("selColDato").value,
      tipo_busqueda:$("selTipoBusqueda").value,
      usuario:estado.usuario, cliente:$("fCliente").value, pais:$("selPais").value,
      umbral:umbralElegido("fUmbral"), proveedores_osint:proveedores,
      limite_interacciones_osint:limiteInteraccionesOsint()})});
  const j = await r.json();
  if(!r.ok){ alert(j.detail||"Error al iniciar el proceso"); return; }
  arrancarSeguimiento(j.job_id);
}

/* ---------- archivo plano ---------- */
$("btnIniciarArchivo").onclick = async ()=>{
  const f = $("fArchivo").files[0];
  if(!f){ alert("Elegí un archivo CSV o Excel primero."); return; }
  estado.esArchivo = true;
  const fd = new FormData();
  fd.append("proceso", estado.proceso);
  fd.append("pais", $("selPaisArch").value);
  fd.append("umbral", umbralElegido("fUmbralArch"));
  fd.append("tipo_busqueda", $("selTipoBusqueda") ? $("selTipoBusqueda").value : "cuit");
  const proveedores = [...document.querySelectorAll('input[name="proveedorOsint"]:checked')]
    .map(el=>el.value);
  if(estado.proceso==="osint" && proveedores.length===0){
    alert("Elegí al menos un proveedor OSINT.");
    return;
  }
  if(estado.proceso==="osint" && !actualizarLimiteOsint()){
    alert(`El máximo permitido es ${LIMITE_INTERACCIONES_OSINT.toLocaleString("es-AR")} interacciones OSINT por ejecución.`);
    return;
  }
  fd.append("proveedores_osint", estado.proceso==="osint" ? proveedores.join(",") : "");
  fd.append("limite_interacciones_osint", limiteInteraccionesOsint());
  fd.append("archivo", f);
  $("btnIniciarArchivo").disabled = true;
  try{
    const r = await fetch("/api/procesos/archivo",{method:"POST", body:fd});
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail||"Error al procesar el archivo");
    arrancarSeguimiento(j.job_id);
  }catch(e){ alert(e.message); }
  finally{ $("btnIniciarArchivo").disabled = false; }
};

$("btnIniciarArchivoN").onclick = async ()=>{
  const f = $("fArchivoN").files[0];
  if(!f){ alert("Elegí un archivo CSV o Excel primero."); return; }
  estado.esArchivo = true;
  const medios = MEDIOS_NORM[estado.proceso] || [];
  const fd = new FormData();
  fd.append("medios", medios.join(","));
  fd.append("archivo", f);
  $("btnIniciarArchivoN").disabled = true;
  try{
    const r = await fetch("/api/normalizacion/archivo",{method:"POST", body:fd});
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail||"Error al normalizar el archivo");
    arrancarSeguimiento(j.job_id);
  }catch(e){ alert(e.message); }
  finally{ $("btnIniciarArchivoN").disabled = false; }
};

$("btnNuevoProceso").onclick = ()=>{
  if(document.querySelector(".navbtn.activo")) reiniciarWizard();
  else irInicio();
};

initOsint();
initPadronSearch();
initProcessRunner();
initHistory({
  onOpenDetail: entrada=>{
    ocultarTodo();
    arrancarSeguimiento(entrada.id);
  },
});
initNavigation({onLoadHome: cargarHistorial});
init();
