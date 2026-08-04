import { $ } from "./core/dom.js";
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
import { initDatabase } from "./features/database.js";
import { initFiles } from "./features/files.js";
import { initCrossReference } from "./features/cross-reference.js";
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
initDatabase({onSelectionReady: actualizarPreview});
initFiles({getThreshold: umbralElegido});
initCrossReference({hideAll: ocultarTodo});
init();
