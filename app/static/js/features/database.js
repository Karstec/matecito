import { $ } from "../core/dom.js";
import { estado } from "../core/state.js";

let actualizarResultado = ()=>{};

async function cargarPresets(){
  const presets = await fetch("/api/presets").then(x=>x.json());
  const selector = $("selPreset");
  selector.innerHTML = '<option value="">— Personalizado —</option>';
  Object.keys(presets).forEach(nombre=>selector.add(new Option(nombre,nombre)));
  selector.dataset.presets = JSON.stringify(presets);
}

function aplicarPreset(){
  const presets = JSON.parse($("selPreset").dataset.presets||"{}");
  const preset = presets[$("selPreset").value];
  if(!preset) return;
  $("fDbType").value = preset.db_type||"oracle";
  $("fHost").value = preset.host||"";
  $("fPort").value = preset.port||"";
  $("fUser").value = preset.user||"";
  $("fDbname").value = preset.dbname||"";
}

async function guardarPreset(){
  const nombre = prompt("Nombre del preset (ej: Mar del Plata):");
  if(!nombre) return;
  await fetch("/api/presets",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({nombre, datos:{
      db_type:$("fDbType").value,
      host:$("fHost").value,
      port:$("fPort").value,
      user:$("fUser").value,
      dbname:$("fDbname").value,
    }}),
  });
  await cargarPresets();
  $("selPreset").value = nombre;
  $("msgConexion").textContent = `Preset "${nombre}" guardado (sin contraseña).`;
}

async function conectar(){
  $("msgConexion").textContent = "Conectando…";
  $("btnConectar").disabled = true;
  try{
    const respuesta = await fetch("/api/conexion",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        db_type:$("fDbType").value,
        host:$("fHost").value,
        port:$("fPort").value,
        user:$("fUser").value,
        password:$("fPass").value,
        dbname:$("fDbname").value,
      }),
    });
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail||"Error de conexión");
    estado.session = datos.session_id;
    $("msgConexion").innerHTML = '<span style="color:var(--verde-osc)">✔ Conectado</span>';
    $("chipEstado").textContent = "conectado";

    const selector = $("selEsquema");
    selector.innerHTML = '<option value="">Elegir…</option>';
    datos.esquemas.forEach(esquema=>selector.add(new Option(esquema,esquema)));
    $("cartaSeleccion").classList.remove("oculto");
    const esDenominacion = estado.proceso==="denominacion" || estado.proceso==="comparacion";
    $("preguntaTabla").classList.toggle("oculto", !esDenominacion);
    $("gridSeleccion").classList.toggle("oculto", esDenominacion);
    $("opMismaTabla").classList.remove("sel");
    $("opTablasDistintas").classList.remove("sel");
    $("avisoTablasDistintas").classList.add("oculto");
  }catch(error){
    $("msgConexion").innerHTML = `<span style="color:var(--rojo)">✖ ${error.message}</span>`;
  }finally{
    $("btnConectar").disabled = false;
  }
}

function configurarPreguntaTabla(){
  $("opMismaTabla").onclick = ()=>{
    $("opMismaTabla").classList.add("sel");
    $("opTablasDistintas").classList.remove("sel");
    $("avisoTablasDistintas").classList.add("oculto");
    $("gridSeleccion").classList.remove("oculto");
  };
  $("opTablasDistintas").onclick = ()=>{
    $("opTablasDistintas").classList.add("sel");
    $("opMismaTabla").classList.remove("sel");
    $("avisoTablasDistintas").classList.remove("oculto");
    $("gridSeleccion").classList.add("oculto");
    $("cartaGeneracion").classList.add("oculto");
  };
  for(const opcion of [$("opMismaTabla"), $("opTablasDistintas")]){
    opcion.onkeydown = evento=>{
      if(evento.key==="Enter"||evento.key===" ") opcion.click();
    };
  }
}

async function cargarTablas(){
  const esquema = $("selEsquema").value;
  const selector = $("selTabla");
  selector.innerHTML = '<option value="">Elegir…</option>';
  selector.disabled = !esquema;
  $("selColId").disabled = true;
  $("selColDato").disabled = true;
  $("btnVerColumnas").disabled = true;
  $("cartaGeneracion").classList.add("oculto");
  if(!esquema) return;
  const datos = await fetch(`/api/conexion/${estado.session}/tablas?esquema=${encodeURIComponent(esquema)}`)
    .then(x=>x.json());
  (datos.tablas||[]).forEach(tabla=>selector.add(new Option(tabla,tabla)));
}

async function cargarColumnas(){
  const esquema = $("selEsquema").value;
  const tabla = $("selTabla").value;
  const identificador = $("selColId");
  const dato = $("selColDato");
  identificador.innerHTML = dato.innerHTML = '<option value="">Elegir…</option>';
  identificador.disabled = dato.disabled = $("btnVerColumnas").disabled = !tabla;
  $("cartaGeneracion").classList.add("oculto");
  if(!tabla) return;
  const respuesta = await fetch(`/api/conexion/${estado.session}/columnas?esquema=${encodeURIComponent(esquema)}&tabla=${encodeURIComponent(tabla)}`);
  const datos = await respuesta.json();
  estado.columnas = datos.columnas||[];
  estado.columnas.forEach(columna=>{
    identificador.add(new Option(columna.nombre,columna.nombre));
    dato.add(new Option(columna.nombre,columna.nombre));
  });
}

function chequearColumnas(){
  const soloUna = estado.proceso==="cuitificacion";
  const listo = soloUna ? !!$("selColId").value : ($("selColId").value && $("selColDato").value);
  if(listo){
    $("cartaGeneracion").classList.remove("oculto");
    actualizarResultado();
  }
}

function mostrarColumnas(){
  const cuerpo = $("tablaColumnas").querySelector("tbody");
  cuerpo.innerHTML = "";
  (estado.columnas||[]).forEach(columna=>{
    const fila = document.createElement("tr");
    fila.innerHTML = `<td>${columna.nombre}</td><td>${columna.tipo||""}</td><td>${columna.largo??""}</td>`;
    cuerpo.appendChild(fila);
  });
  $("veloColumnas").classList.remove("oculto");
}

export function initDatabase({onSelectionReady}){
  actualizarResultado = onSelectionReady;
  $("selPreset").onchange = aplicarPreset;
  $("btnGuardarPreset").onclick = guardarPreset;
  $("btnConectar").onclick = conectar;
  configurarPreguntaTabla();
  $("selEsquema").onchange = cargarTablas;
  $("selTabla").onchange = cargarColumnas;
  $("selColId").onchange = chequearColumnas;
  $("selColDato").onchange = chequearColumnas;
  $("btnVerColumnas").onclick = mostrarColumnas;
  $("btnCerrarColumnas").onclick = ()=>$("veloColumnas").classList.add("oculto");
  cargarPresets();
}
