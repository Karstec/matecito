import { $ } from "../core/dom.js";
import { estado } from "../core/state.js";

const ETIQUETAS = {
  validos:"Válidos", bajas:"Bajas", moviles:"Móviles", fijos:"Fijos",
  conservados:"Conservados", modificados:"Modificados", revision_manual:"Rev. manual",
  coincidentes:"Coincidentes", parciales:"Parciales", sin_coincidencia:"Sin coincidencia",
  cuit_unicos:"CUIT únicos", medios:"Medios (tel + mail)",
  numeros_unicos:"CUIT/DNI únicos", encontrados:"Encontrados", no_encontrados:"Sin match",
  en_revision:"A revisar", validados:"Validados", solo_cuit:"Solo CUIT",
  solo_denom:"Solo denominación", no_coincide:"No coincide", pendientes:"A decidir",
  dni_invalido:"DNI inválido", con_alerta:"Con alerta",
  filas_archivo:"Filas del archivo", filas_base:"Registros de la base",
  pares:"Pares comparados", SI:"Coinciden", RE:"A revisar", NO:"No coinciden",
  sin_candidatos:"Sin candidato", ruido:"Ruido", juridicas:"Jurídicas",
  insertadas:"Filas generadas", csv:"CSV", depurados:"Depurados",
  sin_cambios:"Sin cambios", pais_asumido:"País asumido", sin_numero:"Sin número",
};

export function detenerPoll(){
  if(estado.pollTimer){
    clearInterval(estado.pollTimer);
    estado.pollTimer = null;
  }
}

function descargarCsv(){
  window.location = `/api/procesos/${estado.jobId}/csv`;
}

function mostrarResultado(j){
  const cont = $("statsProceso");
  cont.innerHTML = "";
  const etiquetas = {...ETIQUETAS, total: estado.modo==="normalizacion" ? "Filas resultado" : "Total"};
  for(const [clave, valor] of Object.entries(j.stats||{})){
    const esRuta = typeof valor === "string" && /[\\/]/.test(valor);
    if(esRuta){
      const nombre = String(valor).split(/[\\/]/).pop();
      cont.innerHTML += `<div class="stat"><div class="num ruta" title="${valor}">`
        + `${nombre}</div><div class="lbl">${etiquetas[clave]||clave}</div></div>`;
    }else{
      cont.innerHTML += `<div class="stat"><div class="num">${valor}</div>`
        + `<div class="lbl">${etiquetas[clave]||clave}</div></div>`;
    }
  }
  cont.classList.remove("oculto");
  if(estado.esArchivo){
    descargarCsv();
    $("avisoRutaCsv").innerHTML = `✔ CSV generado y descargado por tu navegador:
      quedó en la carpeta de <b>Descargas de TU PC</b>.<br>
      <span style="color:var(--mudo)">Queda además una copia en la carpeta <b>salidas</b> del
      <b>servidor</b> donde corre MATEcito (no en tu PC). Podés volver a bajarlo cuando quieras
      desde <b>Inicio → historial</b>.</span>`;
    $("avisoRutaCsv").classList.remove("oculto");
    if(j.tiene_csv) $("filaCsvLuego").classList.remove("oculto");
  }else if(j.tiene_csv){
    $("filaCsv").classList.remove("oculto");
  }
}

export function arrancarSeguimiento(jobId){
  estado.jobId = jobId;
  $("cartaProgreso").classList.remove("oculto");
  $("logProceso").textContent = "";
  $("statsProceso").classList.add("oculto");
  $("filaCsv").classList.add("oculto");
  $("filaCsvLuego").classList.add("oculto");
  $("avisoRutaCsv").classList.add("oculto");
  $("chipEstado").textContent = "ejecutando…";
  $("cartaProgreso").scrollIntoView({behavior:"smooth"});
  let desde = 0;
  detenerPoll();
  estado.pollTimer = setInterval(async ()=>{
    const j = await fetch(`/api/procesos/${jobId}?desde=${desde}`).then(x=>x.json());
    if(j.log.length){
      $("logProceso").textContent += j.log.join("\n")+"\n";
      $("logProceso").scrollTop = $("logProceso").scrollHeight;
      desde = j.total_log;
    }
    if(j.estado !== "EN_CURSO"){
      detenerPoll();
      $("chipEstado").textContent = j.estado==="OK" ? "finalizado"
        : (j.estado==="INTERRUMPIDO" ? "interrumpido" : "error");
      if(j.estado==="OK") mostrarResultado(j);
      else if(j.tiene_csv) $("filaCsvLuego").classList.remove("oculto");
    }
  }, 700);
}

export function initProcessRunner(){
  $("btnDescargarCsv").onclick = ()=>{
    descargarCsv();
    $("filaCsv").classList.add("oculto");
    $("filaCsvLuego").classList.remove("oculto");
  };
  $("btnNoCsv").onclick = ()=>{
    $("filaCsv").classList.add("oculto");
    $("filaCsvLuego").classList.remove("oculto");
  };
  $("btnDescargarCsvLuego").onclick = descargarCsv;
}
