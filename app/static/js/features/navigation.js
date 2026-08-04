import { $ } from "../core/dom.js";
import { MEDIOS_NORM, TITULOS } from "../core/process-metadata.js";
import { estado } from "../core/state.js";
import { cargarProveedoresOsint } from "./osint.js";
import { detenerPoll } from "./process-runner.js";

let cargarInicio = async ()=>{};

export function ocultarTodo(){
  ["cartaOrigen","cartaConexion","cartaSeleccion","cartaGeneracion",
   "cartaArchivo","cartaArchivoNorm","cartaProgreso","cartaFuturo","cartaHistorial",
   "cartaCruce"]
   .forEach(id=>$(id).classList.add("oculto"));
}

export async function irInicio(){
  detenerPoll();
  ocultarTodo();
  document.querySelectorAll(".navbtn").forEach(x=>x.classList.remove("activo"));
  $("tituloProceso").textContent = "Inicio — historial de procesos";
  $("chipEstado").textContent = "inicio";
  $("cartaHistorial").classList.remove("oculto");
  await cargarInicio();
}

export function reiniciarWizard(){
  detenerPoll();
  ocultarTodo();
  $("opDB").classList.remove("sel");
  $("opArchivo").classList.remove("sel");
  estado.origen = null;

  if(estado.modo==="normalizacion"){
    estado.origen = "archivo";
    estado.esArchivo = true;
    $("cartaOrigen").classList.add("oculto");
    $("cartaFuturo").classList.add("oculto");
    const medios = MEDIOS_NORM[estado.proceso] || [];
    $("txtMediosNorm").textContent = medios.length===2
      ? "teléfonos y mails"
      : (medios[0]==="telefonos" ? "teléfonos" : "mails");
    $("cartaArchivoNorm").classList.remove("oculto");
    $("chipEstado").textContent = "listo";
    return;
  }

  const disponible = ["mails","osint","telefonos","denominacion","cuitificacion","cuit",
    "comparacion","dep_mails","dep_telefonos"].includes(estado.proceso);
  $("cartaOrigen").classList.toggle("oculto", !disponible);
  $("cartaFuturo").classList.toggle("oculto", disponible);
  const esCuit = estado.proceso==="cuitificacion";
  const esValidCuit = estado.proceso==="cuit";
  if(estado.proceso==="denominacion" || estado.proceso==="comparacion"){
    $("lblColId").textContent = "Columna 1 — denominación origen";
    $("lblColDato").textContent = "Columna 2 — denominación a validar";
  }else if(esCuit){
    $("lblColId").textContent = "Columna del CUIT o DNI";
  }else if(esValidCuit){
    $("lblColId").textContent = "Columna del CUIT o DNI";
    $("lblColDato").textContent = "Columna de la denominación";
  }else{
    $("lblColId").textContent = "Columna del CUIT / identificador";
    $("lblColDato").textContent = (estado.proceso==="mails"||estado.proceso==="dep_mails")
      ? "Columna del mail"
      : (estado.proceso==="dep_telefonos" ? "Columna del teléfono a depurar"
      : (estado.proceso==="osint" ? "Columna del mail a consultar" : "Columna del teléfono a validar"));
  }
  $("grupoColDato").classList.toggle("oculto", esCuit);
  $("avisoCuitificacion").classList.toggle("oculto", !esCuit);
  $("avisoCuit").classList.toggle("oculto", !esValidCuit);
  $("grupoTipoBusqueda").classList.toggle("oculto", !(esCuit || esValidCuit));
  $("grupoPais").classList.toggle("oculto", estado.proceso!=="telefonos");
  $("grupoPaisArch").classList.toggle("oculto", estado.proceso!=="telefonos");
  $("grupoOsint").classList.toggle("oculto", estado.proceso!=="osint");
  if(estado.proceso==="osint") cargarProveedoresOsint();
  const usaUmbral = estado.proceso==="denominacion" || esValidCuit;
  $("grupoUmbral").classList.toggle("oculto", !usaUmbral);
  $("grupoUmbralArch").classList.toggle("oculto", !usaUmbral);
  $("avisoUmbral").classList.toggle("oculto", !usaUmbral);
  $("chipEstado").textContent = "listo";
}

export function initNavigation({onLoadHome}){
  cargarInicio = onLoadHome;
  $("logoInicio").onclick = irInicio;
  $("rolInicio").onclick = irInicio;

  document.querySelectorAll(".navcab").forEach(cab=>{
    cab.onclick = ()=>{
      const grupo = cab.closest(".navgrupo");
      grupo.classList.toggle("abierto");
      const flecha = cab.querySelector(".flecha");
      if(flecha) flecha.textContent = grupo.classList.contains("abierto") ? "▾" : "▸";
    };
  });

  document.querySelectorAll(".navbtn[data-modo][data-proceso]").forEach(boton=>{
    boton.onclick = ()=>{
      document.querySelectorAll(".navbtn").forEach(x=>x.classList.remove("activo"));
      boton.classList.add("activo");
      estado.modo = boton.dataset.modo;
      estado.proceso = boton.dataset.proceso;
      $("tituloProceso").textContent = TITULOS[estado.proceso];
      reiniciarWizard();
    };
  });
}
