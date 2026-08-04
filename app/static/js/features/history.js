import { $ } from "../core/dom.js";
import { TITULOS } from "../core/process-metadata.js";
import { estado } from "../core/state.js";

let abrirDetalle = ()=>{};

function formatearFecha(iso){
  if(!iso) return "—";
  const fecha = new Date(iso);
  const parte = valor=>String(valor).padStart(2,"0");
  return `${parte(fecha.getDate())}/${parte(fecha.getMonth()+1)}/${fecha.getFullYear()} ${parte(fecha.getHours())}:${parte(fecha.getMinutes())}`;
}

function badgeEstado(valor){
  const estados = {
    OK:["badge-ok","Exitoso"],
    ERROR:["badge-error","Falló"],
    EN_CURSO:["badge-curso","En curso"],
    INTERRUMPIDO:["badge-int","Interrumpido"],
  };
  const [clase, texto] = estados[valor] || ["badge-int", valor];
  return `<span class="badge ${clase}">${texto}</span>`;
}

export async function cargarHistorial(){
  const historial = await fetch("/api/historial").then(x=>x.json()).catch(()=>[]);
  const cuerpo = $("cuerpoHistorial");
  cuerpo.innerHTML = "";
  $("histVacio").classList.toggle("oculto", historial.length>0);
  for(const entrada of historial){
    const fila = document.createElement("tr");
    fila.className = "fila-hist";
    fila.title = "Clic para ver el detalle";
    const resultado = entrada.tabla_resultado ||
      (entrada.csv ? "CSV" : (entrada.error ? String(entrada.error).slice(0,50) : "—"));
    const celdaCsv = entrada.tiene_csv
      ? `<button class="btn-mini" data-csv="${entrada.id}" title="Descargar el CSV a tu PC">⬇ Descargar</button>`
      : `<span style="color:var(--mudo); font-size:12px;">—</span>`;
    fila.innerHTML = `<td>${TITULOS[entrada.tipo]||entrada.tipo}</td>
      <td>${entrada.descripcion||"—"}</td>
      <td>${formatearFecha(entrada.fecha_inicio)}</td>
      <td>${entrada.usuario||"—"}</td>
      <td>${badgeEstado(entrada.estado)}</td>
      <td style="font-family:Consolas,monospace; font-size:12px;">${resultado}</td>
      <td>${celdaCsv}</td>`;
    fila.onclick = evento=>{
      if(evento.target.closest("[data-csv]")) return;
      estado.esArchivo = false;
      $("tituloProceso").textContent = `${TITULOS[entrada.tipo]||entrada.tipo} — detalle`;
      abrirDetalle(entrada);
    };
    const boton = fila.querySelector("[data-csv]");
    if(boton) boton.onclick = ()=>{ window.location = `/api/procesos/${entrada.id}/csv`; };
    cuerpo.appendChild(fila);
  }
}

export function initHistory({onOpenDetail}){
  abrirDetalle = onOpenDetail;
  $("btnRefrescarHist").onclick = cargarHistorial;
}
