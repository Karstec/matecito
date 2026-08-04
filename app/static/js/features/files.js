import { avisarColumnasNulas, tablaMini } from "../components/preview-table.js";
import { $ } from "../core/dom.js";
import { MEDIOS_NORM } from "../core/process-metadata.js";
import { estado } from "../core/state.js";
import {
  actualizarLimiteOsint,
  LIMITE_INTERACCIONES_OSINT,
  limiteInteraccionesOsint,
} from "./osint.js";
import { arrancarSeguimiento } from "./process-runner.js";

let obtenerUmbral = ()=>80;

function columnasElegidas(){
  const columnas = [];
  ["selColId","selColDato"].forEach(id=>{
    const elemento = $(id);
    if(elemento && elemento.value && !elemento.closest(".oculto")) columnas.push(elemento.value);
  });
  return [...new Set(columnas)];
}

async function previsualizarTabla(){
  const esquema = $("selEsquema").value;
  const tabla = $("selTabla").value;
  if(!estado.session || !tabla){
    $("prevCuerpo").innerHTML = "<div class='pasoinfo'>Elegí una tabla primero.</div>";
    return;
  }
  $("prevCuerpo").innerHTML = "<div class='pasoinfo'>Leyendo primeros registros…</div>";
  const columnas = columnasElegidas();
  const consulta = `esquema=${encodeURIComponent(esquema)}&tabla=${encodeURIComponent(tabla)}`
    + `&limite=10&columnas=${encodeURIComponent(columnas.join(","))}`;
  try{
    const respuesta = await fetch(`/api/conexion/${estado.session}/muestra?${consulta}`);
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "no se pudo leer");
    const alcance = columnas.length ? columnas.join(", ") : "todas las columnas";
    tablaMini("prevCuerpo", datos.columnas, datos.filas,
      `${esquema ? esquema+"." : ""}${tabla} — primeras ${datos.cantidad} filas · ${alcance}`);
    avisarColumnasNulas("prevCuerpo", datos);
  }catch(error){
    $("prevCuerpo").innerHTML = `<div style="color:#c05;font-size:12px">Error: ${error.message}</div>`;
  }
}

async function previsualizarArchivo(idInput, idCuerpo){
  const archivo = $(idInput).files[0];
  if(!archivo){
    $(idCuerpo).innerHTML = "<div class='pasoinfo'>Elegí un archivo primero.</div>";
    return;
  }
  $(idCuerpo).innerHTML = "<div class='pasoinfo'>Leyendo…</div>";
  const formulario = new FormData();
  formulario.append("archivo", archivo);
  formulario.append("limite", 10);
  try{
    const respuesta = await fetch("/api/archivo/muestra", {method:"POST", body:formulario});
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail || "no se pudo leer");
    if(!datos.cantidad){
      $(idCuerpo).innerHTML = "<div style='color:#c05;font-size:12px'>El archivo no tiene filas.</div>";
      return;
    }
    tablaMini(idCuerpo, datos.columnas, datos.filas,
      `${archivo.name} — primeras ${datos.cantidad} de ${datos.total} filas`);
    avisarColumnasNulas(idCuerpo, datos);
  }catch(error){
    $(idCuerpo).innerHTML = `<div style="color:#c05;font-size:12px">Error: ${error.message}</div>`;
  }
}

async function procesarArchivo(){
  const archivo = $("fArchivo").files[0];
  if(!archivo){
    alert("Elegí un archivo CSV o Excel primero.");
    return;
  }
  estado.esArchivo = true;
  const formulario = new FormData();
  formulario.append("proceso", estado.proceso);
  formulario.append("pais", $("selPaisArch").value);
  formulario.append("umbral", obtenerUmbral("fUmbralArch"));
  formulario.append("tipo_busqueda", $("selTipoBusqueda") ? $("selTipoBusqueda").value : "cuit");
  const proveedores = [...document.querySelectorAll('input[name="proveedorOsint"]:checked')]
    .map(elemento=>elemento.value);
  if(estado.proceso==="osint" && proveedores.length===0){
    alert("Elegí al menos un proveedor OSINT.");
    return;
  }
  if(estado.proceso==="osint" && !actualizarLimiteOsint()){
    alert(`El máximo permitido es ${LIMITE_INTERACCIONES_OSINT.toLocaleString("es-AR")} interacciones OSINT por ejecución.`);
    return;
  }
  formulario.append("proveedores_osint", estado.proceso==="osint" ? proveedores.join(",") : "");
  formulario.append("limite_interacciones_osint", limiteInteraccionesOsint());
  formulario.append("archivo", archivo);
  $("btnIniciarArchivo").disabled = true;
  try{
    const respuesta = await fetch("/api/procesos/archivo", {method:"POST", body:formulario});
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail||"Error al procesar el archivo");
    arrancarSeguimiento(datos.job_id);
  }catch(error){
    alert(error.message);
  }finally{
    $("btnIniciarArchivo").disabled = false;
  }
}

async function normalizarArchivo(){
  const archivo = $("fArchivoN").files[0];
  if(!archivo){
    alert("Elegí un archivo CSV o Excel primero.");
    return;
  }
  estado.esArchivo = true;
  const formulario = new FormData();
  formulario.append("medios", (MEDIOS_NORM[estado.proceso] || []).join(","));
  formulario.append("archivo", archivo);
  $("btnIniciarArchivoN").disabled = true;
  try{
    const respuesta = await fetch("/api/normalizacion/archivo", {method:"POST", body:formulario});
    const datos = await respuesta.json();
    if(!respuesta.ok) throw new Error(datos.detail||"Error al normalizar el archivo");
    arrancarSeguimiento(datos.job_id);
  }catch(error){
    alert(error.message);
  }finally{
    $("btnIniciarArchivoN").disabled = false;
  }
}

export function initFiles({getThreshold}){
  obtenerUmbral = getThreshold;
  $("btnPrevisualizar").onclick = previsualizarTabla;
  ["selEsquema","selTabla","selColId","selColDato"].forEach(id=>{
    $(id).addEventListener("change", ()=>{
      if($("prevCuerpo").innerHTML.trim()) previsualizarTabla();
    });
  });
  $("btnPrevArch").onclick = ()=>previsualizarArchivo("fArchivo", "prevArchCuerpo");
  $("btnPrevArchN").onclick = ()=>previsualizarArchivo("fArchivoN", "prevArchCuerpoN");
  [["fArchivo","prevArchCuerpo"],["fArchivoN","prevArchCuerpoN"]].forEach(([entrada,cuerpo])=>{
    $(entrada).addEventListener("change", ()=>{ $(cuerpo).innerHTML = ""; });
  });
  $("btnIniciarArchivo").onclick = procesarArchivo;
  $("btnIniciarArchivoN").onclick = normalizarArchivo;
}
