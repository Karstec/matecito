import { avisarColumnasNulas, tablaMini } from "../components/preview-table.js";
import { $ } from "../core/dom.js";
import { estado } from "../core/state.js";
import { arrancarSeguimiento, detenerPoll } from "./process-runner.js";

const cruce = {token:null, columnas:[], sid:null, muestraOk:false};
let ocultarPantallas = ()=>{};

function opciones(id, valores, permitirVacio){
  const selector = $(id);
  if(!selector) return;
  selector.innerHTML = "";
  if(permitirVacio) selector.appendChild(new Option("(ninguna)",""));
  valores.forEach(valor=>selector.appendChild(new Option(valor,valor)));
}

function elegirPorNombre(id, candidatos){
  const selector = $(id);
  if(!selector) return;
  for(const candidato of candidatos){
    for(const opcion of selector.options){
      if(opcion.value.toUpperCase().includes(candidato)){
        selector.value = opcion.value;
        return;
      }
    }
  }
}

function pasoActivo(id, activo){
  $(id).classList.toggle("bloqueado", !activo);
}

function revisarListo(){
  const listo = !!cruce.token && !!cruce.sid && !!$("cruceTabla").value
    && !!$("cruceColNombreBase").value && cruce.muestraOk;
  $("btnCruceEjecutar").disabled = !listo;
  $("btnCruceEjecutar").title = listo ? ""
    : "Completá los 3 pasos: archivo, base y confirmación de las primeras filas.";
}

function abrirCruce(){
  detenerPoll();
  ocultarPantallas();
  document.querySelectorAll(".navbtn").forEach(elemento=>elemento.classList.remove("activo"));
  $("btnAbrirCruce").classList.add("activo");
  $("tituloProceso").textContent = "Cruce de denominaciones";
  $("chipEstado").textContent = "configurando";
  $("cartaCruce").classList.remove("oculto");
  $("cruceAviso").textContent = "";
  $("btnCruceUsarActual").style.display = estado.session ? "" : "none";
  revisarListo();
}

async function subirArchivo(){
  const archivo = $("cruceArchivo").files[0];
  if(!archivo){
    $("cruceInfoArchivo").textContent = "Elegí un archivo primero.";
    return;
  }
  $("cruceInfoArchivo").textContent = "Leyendo…";
  const formulario = new FormData();
  formulario.append("archivo", archivo);
  try{
    const respuesta = await fetch("/api/cruce-redes/subir", {method:"POST", body:formulario});
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "no se pudo leer");
    cruce.token = datos.token;
    cruce.columnas = datos.columnas;
    cruce.muestraOk = false;
    $("cruceInfoArchivo").textContent =
      `${datos.archivo} · ${datos.filas} filas · ${datos.columnas.length} columnas`;
    ["cruceColNombre","cruceColId"].forEach(id=>opciones(id, datos.columnas, false));
    ["cruceColUser","cruceColTel","cruceColMail"].forEach(id=>opciones(id, datos.columnas, true));
    elegirPorNombre("cruceColNombre", ["NOMBRE","NAME","DENOMIN"]);
    elegirPorNombre("cruceColId", ["N","ID"]);
    elegirPorNombre("cruceColUser", ["USERNAME","USUARIO","PERFIL"]);
    elegirPorNombre("cruceColTel", ["TELEFONO","TEL","PHONE","CELULAR"]);
    elegirPorNombre("cruceColMail", ["EMAIL","MAIL","CORREO"]);
    $("cruceBloqueCols").classList.remove("oculto");
    pasoActivo("pasoBase", true);
  }catch(error){
    $("cruceInfoArchivo").innerHTML = `<span style="color:#c05">Error: ${error.message}</span>`;
    cruce.token = null;
    pasoActivo("pasoBase", false);
  }
  revisarListo();
}

async function cargarTablas(){
  const esquema = $("cruceEsquema").value;
  if(!esquema || !cruce.sid) return;
  const respuesta = await fetch(`/api/conexion/${cruce.sid}/tablas?esquema=${encodeURIComponent(esquema)}`);
  const datos = await respuesta.json();
  opciones("cruceTabla", datos.tablas || [], false);
  await cargarColumnas();
}

async function cargarColumnas(){
  const esquema = $("cruceEsquema").value;
  const tabla = $("cruceTabla").value;
  if(!esquema || !tabla || !cruce.sid) return;
  const respuesta = await fetch(`/api/conexion/${cruce.sid}/columnas?esquema=${encodeURIComponent(esquema)}&tabla=${encodeURIComponent(tabla)}`);
  const datos = await respuesta.json();
  const columnas = (datos.columnas || []).map(columna=>
    typeof columna === "string" ? columna : (columna.nombre || columna[0]));
  opciones("cruceColIdBase", columnas, false);
  opciones("cruceColNombreBase", columnas, false);
  opciones("cruceColDoc", columnas, true);
  elegirPorNombre("cruceColIdBase", ["CONCOD","ID","COD"]);
  elegirPorNombre("cruceColNombreBase", ["NOMCOMPLETO","NOMBRE","DENOMIN"]);
  elegirPorNombre("cruceColDoc", ["DOC","DNI","RUT","CUIT","CI"]);
  cruce.muestraOk = false;
  revisarListo();
}

async function cargarEsquemas(){
  if(!$("cruceEsquema").options.length && cruce.sid){
    await fetch(`/api/conexion/${cruce.sid}/tablas?esquema=`);
  }
  await cargarTablas();
}

async function tomarSesion(sid, etiqueta){
  cruce.sid = sid;
  cruce.muestraOk = false;
  $("cruceMsgConexion").innerHTML = `<span style="color:var(--verde-osc)">✔ ${etiqueta}</span>`;
  $("cruceBloqueSeleccion").classList.remove("oculto");
  await fetch(`/api/conexion/${sid}/tablas?esquema=`);
  await cargarEsquemas();
  pasoActivo("pasoPrev", true);
  revisarListo();
}

async function conectar(){
  $("cruceMsgConexion").textContent = "Conectando…";
  try{
    const respuesta = await fetch("/api/conexion",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        db_type:$("cxDbType").value,
        host:$("cxHost").value,
        port:$("cxPort").value,
        user:$("cxUser").value,
        password:$("cxPass").value,
        dbname:$("cxDbname").value,
      }),
    });
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "no se pudo conectar");
    opciones("cruceEsquema", datos.esquemas || [], false);
    $("cxPass").value = "";
    await tomarSesion(datos.session_id, "Conectado");
  }catch(error){
    $("cruceMsgConexion").innerHTML = `<span style="color:#c05">${error.message}</span>`;
    cruce.sid = null;
    pasoActivo("pasoPrev", false);
    revisarListo();
  }
}

function usarSesionActual(){
  if(!estado.session) return;
  const origen = $("selEsquema");
  opciones("cruceEsquema", [...origen.options].map(opcion=>opcion.value).filter(Boolean), false);
  if(origen.value) $("cruceEsquema").value = origen.value;
  tomarSesion(estado.session, "Usando la conexión de la pantalla principal");
}

async function previsualizar(){
  $("crucePrevBase").innerHTML = "Leyendo…";
  try{
    const respuesta = await fetch(`/api/cruce-redes/columnas-archivo?ruta=${encodeURIComponent(cruce.token)}`);
    const datos = await respuesta.json();
    if(respuesta.ok && datos.muestra){
      tablaMini("crucePrevArchivo", datos.columnas, datos.muestra, "Archivo — primeras filas");
    }else{
      $("crucePrevArchivo").innerHTML =
        `<div class="minicab">Archivo</div><div class="pasoinfo">${cruce.columnas.join(" · ")}</div>`;
    }
  }catch(error){
    $("crucePrevArchivo").innerHTML = "";
  }

  const columnas = [$("cruceColIdBase").value, $("cruceColNombreBase").value,
    $("cruceColDoc").value].filter(Boolean);
  const consulta = `esquema=${encodeURIComponent($("cruceEsquema").value)}`
    + `&tabla=${encodeURIComponent($("cruceTabla").value)}`
    + `&limite=10&columnas=${encodeURIComponent(columnas.join(","))}`;
  try{
    const respuesta = await fetch(`/api/conexion/${cruce.sid}/muestra?${consulta}`);
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "no se pudo leer");
    tablaMini("crucePrevBase", datos.columnas, datos.filas,
      `Base — ${$("cruceTabla").value}, primeras ${datos.cantidad} filas`);
    avisarColumnasNulas("crucePrevBase", datos);
    cruce.muestraOk = true;
    revisarListo();
  }catch(error){
    $("crucePrevBase").innerHTML = `<span style="color:#c05">Error: ${error.message}</span>`;
  }
}

async function ejecutar(){
  const idBase = $("cruceColIdBase").value;
  const nombreBase = $("cruceColNombreBase").value;
  if(idBase === nombreBase){
    $("cruceAviso").innerHTML =
      "<span style='color:#c05'>El identificador y el nombre no pueden ser la misma columna.</span>";
    return;
  }
  const formulario = new FormData();
  formulario.append("session_id", cruce.sid);
  formulario.append("token", cruce.token);
  formulario.append("origen", "archivo");
  formulario.append("esquema", $("cruceEsquema").value);
  formulario.append("tabla_base", $("cruceTabla").value);
  formulario.append("col_id_base", idBase);
  formulario.append("col_denom_base", nombreBase);
  formulario.append("col_doc_base", $("cruceColDoc").value);
  formulario.append("col_denom_archivo", $("cruceColNombre").value);
  formulario.append("col_id_archivo", $("cruceColId").value);
  formulario.append("col_usuario", $("cruceColUser").value);
  formulario.append("col_telefono", $("cruceColTel").value);
  formulario.append("col_mail", $("cruceColMail").value);
  formulario.append("where_base", $("cruceWhere").value);
  formulario.append("candidatos_por_fila", $("cruceCandidatos").value);
  formulario.append("usuario", estado.usuario || "MATECITO");
  formulario.append("cliente", $("cruceTabla").value || "CRUCE");

  $("btnCruceEjecutar").disabled = true;
  $("cruceAviso").textContent = "Lanzando el cruce…";
  try{
    const respuesta = await fetch("/api/cruce-redes/ejecutar", {method:"POST", body:formulario});
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "falló el cruce");
    cruce.token = null;
    cruce.muestraOk = false;
    ocultarPantallas();
    arrancarSeguimiento(datos.job_id);
  }catch(error){
    $("cruceAviso").innerHTML = `<span style="color:#c05">Error: ${error.message}</span>`;
    revisarListo();
  }
}

export function initCrossReference({hideAll}){
  ocultarPantallas = hideAll;
  $("btnAbrirCruce").onclick = abrirCruce;
  $("btnCruceSubir").onclick = subirArchivo;
  $("btnCruceConectar").onclick = conectar;
  $("btnCruceUsarActual").onclick = usarSesionActual;
  $("cruceEsquema").onchange = cargarTablas;
  $("cruceTabla").onchange = cargarColumnas;
  ["cruceColNombreBase","cruceColIdBase","cruceColDoc"].forEach(id=>{
    $(id).addEventListener("change", ()=>{
      cruce.muestraOk = false;
      revisarListo();
    });
  });
  $("btnCrucePrev").onclick = previsualizar;
  $("btnCruceEjecutar").onclick = ejecutar;
}
