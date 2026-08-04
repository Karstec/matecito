import { $ } from "../core/dom.js";
import { estado } from "../core/state.js";

async function buscarPadron(){
  const num = $("fBuscar").value.trim();
  if(!num) return;

  const cont = $("resBuscador");
  cont.innerHTML = `<div class="aviso aviso-ambar">Buscando en el padrón…</div>`;
  $("btnBuscarPadron").disabled = true;
  try{
    const sid = estado.session ? `&sid=${encodeURIComponent(estado.session)}` : "";
    const r = await fetch(`/api/padron/buscar?numero=${encodeURIComponent(num)}${sid}`);
    const j = await r.json();
    if(!r.ok){
      cont.innerHTML = `<div class="aviso aviso-rojo">${j.detail||"Error al consultar"}</div>`;
      return;
    }
    if(!j.encontrados){
      cont.innerHTML = `<div class="aviso aviso-rojo">
        No se encontró ningún registro con <b>${j.numero}</b> en el padrón BCRA.</div>`;
      return;
    }

    let html = `<div style="font-size:13px; color:var(--mudo); margin-bottom:6px">
       ${j.encontrados} coincidencia(s) para <b>${j.numero}</b>`;
    if(j.truncado) html += ` — se muestran las primeras ${j.limite}, hay más`;
    html += `</div><div style="max-height:340px; overflow:auto">
      <table class="tabla"><thead><tr>
      <th>CUIT</th><th>DNI</th><th>Denominación</th><th>Sexo</th>
      <th>F. nac.</th><th>Provincia</th><th>Baja</th></tr></thead><tbody>`;
    for(const f of j.filas){
      const baja = (f.MARCA_BAJA||"").trim();
      const fall = (f.FECHA_FALLECIMIENTO||"").trim();
      let alerta = "—";
      if(fall) alerta = `<span style="color:var(--rojo)">† ${fall}</span>`;
      else if(baja === "*") alerta = `<span style="color:var(--ambar)">baja</span>`;
      html += `<tr>
        <td style="font-family:Consolas,monospace">${f.CUIT||"—"}</td>
        <td style="font-family:Consolas,monospace">${f.DNI||"—"}</td>
        <td>${f.DENOMINACION||f.NOMBRE_LIMPIO||"—"}</td>
        <td>${f.SEXO||"—"}</td>
        <td>${f.FECHA_NACIMIENTO||"—"}</td>
        <td>${f.PROVINCIA||"—"}</td>
        <td>${alerta}</td></tr>`;
    }
    cont.innerHTML = html + "</tbody></table></div>";
  }catch(e){
    cont.innerHTML = `<div class="aviso aviso-rojo">No se pudo consultar el padrón: ${e}</div>`;
  }finally{
    $("btnBuscarPadron").disabled = false;
  }
}

export function initPadronSearch(){
  $("btnAbrirBuscador").onclick = ()=>{
    $("veloBuscador").classList.remove("oculto");
    $("resBuscador").innerHTML = "";
    $("fBuscar").value = "";
    $("fBuscar").focus();
  };
  $("btnCerrarBuscador").onclick = ()=>$("veloBuscador").classList.add("oculto");
  $("fBuscar").onkeydown = e=>{ if(e.key==="Enter") buscarPadron(); };
  $("btnBuscarPadron").onclick = buscarPadron;
}
