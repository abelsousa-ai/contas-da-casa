// Subcategorias: mostrar só as da categoria escolhida
document.querySelectorAll('select[data-categoria]').forEach(function (cat) {
  const form = cat.closest('form'); const sub = form && form.querySelector('select[data-subcategoria]');
  if (!sub) return;
  function filtrar() {
    sub.querySelectorAll('option[data-de]').forEach(function (o) {
      const visivel = o.dataset.de === cat.value; o.hidden = !visivel; o.disabled = !visivel;
      if (!visivel && o.selected) sub.value = '';
    });
  }
  cat.addEventListener('change', filtrar); filtrar();
});

const EUR = new Intl.NumberFormat('pt-PT', { style: 'currency', currency: 'EUR' });
function cores() {
  const s = getComputedStyle(document.documentElement);
  return { tinta: s.getPropertyValue('--tinta-2').trim(), linha: s.getPropertyValue('--linha').trim(),
           bom: s.getPropertyValue('--bom').trim(), mau: s.getPropertyValue('--mau').trim() };
}

function iniciarAnalises() {
  if (!window.Chart) return;
  const c = cores();
  Chart.defaults.font.family = "'Plex', system-ui, sans-serif";
  Chart.defaults.color = c.tinta;
  fetch(FIN.url).then(r => r.json()).then(d => {
    if (FIN.vista === 'tendencia') return tendencia(d, c);
    colunas(d, c);
    if (FIN.vista === 'anual') queijos(d, c);
  });
}

const MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
const rotuloPeriodo = p => p.length === 7 ? MESES[+p.slice(5) - 1] + ' ' + p.slice(2, 4) : p;
function colunas(d, c) {
  const ds = d.categorias.map(cat => ({ label: cat.nome, data: cat.valores, backgroundColor: cat.cor, borderRadius: 2, maxBarThickness: 28 }));
  const g = new Chart(document.getElementById('colunas'), {
    type: 'bar', data: { labels: d.periodos.map(rotuloPeriodo), datasets: ds },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: x => x.dataset.label + ': ' + EUR.format(x.raw),
          footer: xs => 'Total visível: ' + EUR.format(xs.reduce((s, x) => s + x.raw, 0)) } } },
      scales: { x: { stacked: false, grid: { display: false } },
                y: { stacked: false, grid: { color: c.linha }, ticks: { callback: v => EUR.format(v).replace(',00', '') } } } }
  });
  const filtro = document.getElementById('filtro');
  function desenhar() {
    filtro.innerHTML = '';
    ds.forEach((s, i) => {
      const chip = document.createElement('span'); chip.className = 'chip' + (g.isDatasetVisible(i) ? '' : ' off');
      chip.setAttribute('role', 'button'); chip.tabIndex = 0;
      chip.innerHTML = '<i style="background:' + s.backgroundColor + '"></i>' + s.label + ' <button class="so" type="button">só</button>';
      chip.addEventListener('click', e => {
        if (e.target.classList.contains('so')) ds.forEach((_, j) => g.setDatasetVisibility(j, j === i));
        else g.setDatasetVisibility(i, !g.isDatasetVisible(i));
        g.update(); desenhar();
      });
      chip.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chip.click(); } });
      filtro.appendChild(chip);
    });
  }
  desenhar();
  document.getElementById('empilhar').addEventListener('change', e => {
    g.options.scales.x.stacked = g.options.scales.y.stacked = e.target.checked; g.update();
  });
  document.getElementById('todas').onclick = () => { ds.forEach((_, j) => g.setDatasetVisibility(j, true)); g.update(); desenhar(); };
  document.getElementById('nenhuma').onclick = () => { ds.forEach((_, j) => g.setDatasetVisibility(j, false)); g.update(); desenhar(); };
}

function queijos(d, c) {
  const cor = Object.fromEntries(d.categorias.map(x => [x.nome, x.cor]));
  const caixa = document.getElementById('queijos');
  d.queijos.slice().reverse().forEach(q => {
    const el = document.createElement('div'); el.className = 'queijo';
    el.innerHTML = '<h3>' + q.periodo + '</h3><div class="tela"><canvas role="img" aria-label="Peso das categorias em ' + q.periodo + '"></canvas></div>' +
      '<ol class="nota" style="padding-left:18px;margin:8px 0 0">' + q.fatias.slice(0, 5).map(f => '<li>' + f.categoria + ': ' + f.pct.toLocaleString('pt-PT') + '%</li>').join('') + '</ol>' +
      (q.fora.length ? '<p class="nota">Não incluído: ' + q.fora.map(f => f.categoria + ' (+' + EUR.format(f.valor) + ')').join(', ') + '</p>' : '');
    caixa.appendChild(el);
    new Chart(el.querySelector('canvas'), { type: 'doughnut',
      data: { labels: q.fatias.map(f => f.categoria), datasets: [{ data: q.fatias.map(f => f.pct), backgroundColor: q.fatias.map(f => cor[f.categoria] || '#999'), borderWidth: 1 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: '45%', plugins: { legend: { display: false },
        tooltip: { callbacks: { label: x => x.label + ': ' + x.raw.toLocaleString('pt-PT') + '% (' + EUR.format(q.fatias[x.dataIndex].valor) + ')' } } } } });
  });
}

const pct = v => (v > 0 ? '+' : '') + v.toLocaleString('pt-PT') + '%';
function tendencia(d, c) {
  const n = d.meses.length;
  const recta = t => d.meses.map((_, i) => t.inicio + (t.fim - t.inicio) * i / (n - 1));
  const fimTexto = {
    id: 'fimTexto',
    afterDatasetsDraw(ch) {
      const ctx = ch.ctx; ctx.save(); ctx.font = "600 13px 'Plex', system-ui"; ctx.textAlign = 'right';
      const r = ch.getDatasetMeta(2).data[n - 1], k = ch.getDatasetMeta(3).data[n - 1];
      if (!r || !k) return;
      const cima = r.y <= k.y;   // a etiqueta da linha de cima vai por cima, a de baixo por baixo
      [[r, d.tendencia_rendimento, c.bom, cima], [k, d.tendencia_custos, c.mau, !cima]].forEach(([p, t, cor, acima]) => {
        if (t.pct === null) return;
        ctx.fillStyle = cor; ctx.fillText(pct(t.pct), p.x - 4, acima ? p.y - 10 : p.y + 20);
      });
      ctx.restore();
    }
  };
  new Chart(document.getElementById('tendencia'), {
    type: 'line', plugins: [fimTexto],
    data: { labels: d.meses.map(rotuloPeriodo), datasets: [
      { label: 'Rendimento', data: d.rendimento, borderColor: c.bom, backgroundColor: c.bom + '33', fill: 'origin', pointRadius: 0, borderWidth: 1.5, tension: 0 },
      { label: 'Custos', data: d.custos, borderColor: c.mau, backgroundColor: c.mau + '33', fill: 'origin', pointRadius: 0, borderWidth: 1.5, tension: 0 },
      { label: 'Tendência do rendimento (' + pct(d.tendencia_rendimento.pct) + ')', data: recta(d.tendencia_rendimento), borderColor: c.bom, borderDash: [6, 4], pointRadius: 0, borderWidth: 2, fill: false },
      { label: 'Tendência dos custos (' + pct(d.tendencia_custos.pct) + ')', data: recta(d.tendencia_custos), borderColor: c.mau, borderDash: [6, 4], pointRadius: 0, borderWidth: 2, fill: false } ] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } },
        tooltip: { callbacks: { label: x => x.dataset.label + ': ' + EUR.format(x.raw) } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
                y: { grid: { color: c.linha }, ticks: { callback: v => EUR.format(v).replace(',00', '') } } } }
  });
}
