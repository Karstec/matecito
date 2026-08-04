import { $ } from "../core/dom.js";

export function avisarColumnasNulas(contenedor, datos){
  const nulas = (datos.diagnostico || [])
    .filter(columna=>columna.nulos === datos.cantidad)
    .map(columna=>columna.columna);
  if(!nulas.length) return;
  $(contenedor).innerHTML +=
    `<div style="color:#c05;font-size:12px;margin-top:6px">⚠ Vienen todas nulas: `
    + `<b>${nulas.join(", ")}</b>. Revisá si es la columna correcta.</div>`;
}

export function tablaMini(contenedor, columnas, filas, titulo){
  if(!filas.length){
    $(contenedor).innerHTML =
      `<div class="minicab">${titulo}</div><div style="color:#c05;font-size:12px">Sin filas.</div>`;
    return;
  }
  let html = `<div class="minicab">${titulo}</div><div class="miniwrap"><table class="minitabla"><thead><tr>`;
  html += columnas.map(columna=>`<th>${String(columna).replace(/</g,"&lt;")}</th>`).join("");
  html += "</tr></thead><tbody>";
  filas.forEach(fila=>{
    html += "<tr>" + fila.map(valor=>{
      if(valor === null || valor === undefined) return "<td class='nulo'>(null)</td>";
      if(String(valor) === "") return "<td class='nulo'>(vacío)</td>";
      return `<td>${String(valor).replace(/&/g,"&amp;").replace(/</g,"&lt;")}</td>`;
    }).join("") + "</tr>";
  });
  $(contenedor).innerHTML = html + "</tbody></table></div>";
}
