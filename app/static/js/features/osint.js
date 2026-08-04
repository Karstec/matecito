import { $ } from "../core/dom.js";

let proveedoresCargados = false;
export const LIMITE_INTERACCIONES_OSINT = 20000;

export function limiteInteraccionesOsint(){
  const valor = parseInt($("limiteInteraccionesOsint").value, 10);
  return Number.isInteger(valor) ? valor : LIMITE_INTERACCIONES_OSINT;
}

export function actualizarLimiteOsint(){
  const limite = limiteInteraccionesOsint();
  const proveedores = document.querySelectorAll('input[name="proveedorOsint"]:checked').length;
  const aviso = $("avisoLimiteOsint");
  if(limite < 1 || limite > LIMITE_INTERACCIONES_OSINT){
    aviso.className = "aviso aviso-rojo";
    aviso.textContent = `El máximo permitido es ${LIMITE_INTERACCIONES_OSINT.toLocaleString("es-AR")} interacciones por ejecución.`;
    return false;
  }
  const correos = proveedores ? Math.floor(limite / proveedores) : 0;
  aviso.className = "aviso aviso-verde";
  aviso.textContent = proveedores
    ? `Con ${proveedores} proveedor${proveedores===1 ? "" : "es"}, se consultarán hasta ${correos.toLocaleString("es-AR")} mails únicos (${limite.toLocaleString("es-AR")} interacciones como máximo).`
    : `Tope seguro: ${limite.toLocaleString("es-AR")} interacciones por ejecución. Elegí proveedores para calcular los mails posibles.`;
  return true;
}

function actualizarSeleccion(){
  const checks = [...document.querySelectorAll('input[name="proveedorOsint"]')];
  checks.forEach(c=>c.closest("label").classList.toggle("sel", c.checked));
  const cantidad = checks.filter(c=>c.checked).length;
  $("contadorProveedoresOsint").textContent =
    `${cantidad} seleccionado${cantidad===1 ? "" : "s"}`;
  actualizarLimiteOsint();
}

function filtrarProveedores(){
  const filtro = $("buscarProveedorOsint").value.trim().toLowerCase();
  document.querySelectorAll("#proveedoresOsint label").forEach(label=>{
    label.classList.toggle("oculto", !label.dataset.busqueda.includes(filtro));
  });
}

export async function cargarProveedoresOsint(){
  if(proveedoresCargados) return;
  const contenedor = $("proveedoresOsint");
  contenedor.innerHTML = "<span>Cargando proveedores…</span>";
  try{
    const r = await fetch("/api/osint/proveedores");
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail||"OSINT no disponible");
    contenedor.innerHTML = j.proveedores.map(p =>
      `<label class="chk" data-busqueda="${`${p.nombre} ${p.categoria}`.toLowerCase()}">
       <input type="checkbox" name="proveedorOsint" value="${p.id}">
       ${p.nombre} <small>(${p.categoria})</small></label>`).join("");
    contenedor.querySelectorAll('input[name="proveedorOsint"]').forEach(
      check=>check.onchange = actualizarSeleccion
    );
    proveedoresCargados = true;
    actualizarSeleccion();
  }catch(e){
    contenedor.innerHTML = `<span>${e.message}</span>`;
  }
}

export function initOsint(){
  $("buscarProveedorOsint").oninput = filtrarProveedores;
  $("limiteInteraccionesOsint").oninput = actualizarLimiteOsint;
  $("btnSeleccionarVisibles").onclick = ()=>{
    document.querySelectorAll("#proveedoresOsint label:not(.oculto) input").forEach(
      check=>check.checked = true
    );
    actualizarSeleccion();
  };
  $("btnLimpiarProveedores").onclick = ()=>{
    document.querySelectorAll('input[name="proveedorOsint"]').forEach(
      check=>check.checked = false
    );
    actualizarSeleccion();
  };
}
