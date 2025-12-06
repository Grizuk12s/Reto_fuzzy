# app_fastapi.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from db_connection import crear_conexion, leer_entradas, leer_salidas, insertar_salida_random

app = FastAPI(title="Reto Fuzzy API")

tiempo_refresco = 2   # valor por defecto

@app.get("/config")
def obtener_configuracion():
    return {"tiempo_refresco": tiempo_refresco}

@app.post("/config/{nuevo_valor}")
def actualizar_configuracion(nuevo_valor: int):
    global tiempo_refresco
    tiempo_refresco = nuevo_valor
    return {"nuevo_tiempo_refresco": tiempo_refresco}


@app.get("/")
def root():
    return {"mensaje": "API Reto Fuzzy OK. Visita /docs para ver los endpoints."}


@app.get("/entradas")
def get_entradas():
    conn = crear_conexion()
    try:
        filas = leer_entradas(conn)
        return [
            {"id_entradas": f[0], "n1": f[1], "n2": f[2]}
            for f in filas
        ]
    finally:
        conn.close()


@app.get("/salidas")
def get_salidas():
    conn = crear_conexion()
    try:
        filas = leer_salidas(conn)
        return [
            {"id_salidas": f[0], "n1": f[1], "n2": f[2]}
            for f in filas
        ]
    finally:
        conn.close()

@app.post("/salidas/random")
def crear_salida_random():
    conn = crear_conexion()
    try:
        n1, n2 = insertar_salida_random(conn)
        return {"status": "ok", "n1": n1, "n2": n2}
    finally:
        conn.close()

@app.get("/panel", response_class=HTMLResponse)
def panel():
    html = """
    <html>
    <head>
        <title>Reto Fuzzy Panel</title>
        <style>
            body {
                margin: 0;
                padding: 40px 0;
                font-family: Arial, Helvetica, sans-serif;
                background-color: #232b3b;
                color: #f5f5f5;
                display: flex;
                flex-direction: column;
                align-items: center;
            }
            h1 { font-size: 32px; margin-bottom: 10px; }
            #contador { font-size: 16px; color: #1abc9c; margin-bottom: 20px; }

            .table-container {
                background-color: #343f57;
                border-radius: 10px;
                box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5);
                padding: 20px 30px;
                margin-bottom: 30px;
                width: 80%;
                max-width: 900px;
            }

            .table-container h2 { text-align: center; }

            table {
                width: 100%;
                border-collapse: collapse;
            }

            th, td {
                padding: 10px 12px;
                text-align: center;
            }

            th { background-color: #1abc9c; color: #ffffff; }

            tr:nth-child(even)  { background-color: rgba(255, 255, 255, 0.04); }
            tr:nth-child(odd)   { background-color: rgba(0, 0, 0, 0.15); }

            .bool-true {
                background-color: #2ecc71;
                color: #ffffff;
                font-weight: bold;
            }
            .bool-false {
                background-color: #7f8c8d;
                color: #ffffff;
                font-weight: bold;
            }
        </style>
    </head>

    <body>
        <h1>Reto Fuzzy - Panel</h1>
        <div id="contador">Actualización en: <span id="segundos">10</span>s</div>

        <div class="table-container">
            <h2>Entradas (últimos 3)</h2>
            <table>
                <thead><tr><th>n1</th><th>n2</th></tr></thead>  <!-- ID oculto -->
                <tbody id="entradas-body"></tbody>
            </table>
        </div>

        <div class="table-container">
            <h2>Salidas (últimos 3)</h2>
            <table>
                <thead><tr><th>n1</th><th>n2</th></tr></thead>  <!-- ID oculto -->
                <tbody id="salidas-body"></tbody>
            </table>
        </div>

        <script>
            let intervalo = 10;           // valor temporal antes de cargar desde el backend
            let tiempoActual = 10;        // variable real del backend
            
            // Obtener el tiempo de refresco desde /config
            fetch("http://127.0.0.1:8000/config")
            .then(resp => resp.json())
            .then(cfg => {
                tiempoActual = cfg.tiempo_refresco;  // tiempo real desde FastAPI
                intervalo = tiempoActual;            // iniciar contador con ese valor
                console.log("Tiempo refresco:", tiempoActual);
            })
            .catch(err => console.error("Error cargando configuración:", err));


            // 0 -> rojo, 50 -> verde (interpolamos en HSL de 0° a 120°)
            function getColorForValue(value) {
                const min = 0;
                const max = 50;
                let v = Number(value);
                if (isNaN(v)) v = 0;
                if (v < min) v = min;
                if (v > max) v = max;

                const t = v / (max - min);   // 0..1
                const hue = 0 + t * 120;     // 0 (rojo) a 120 (verde)
                return `hsl(${hue}, 70%, 50%)`;
            }

            function actualizarContador() {
                document.getElementById("segundos").innerText = intervalo;
                intervalo--;
            
                if (intervalo < 0) {
                    intervalo = tiempoActual;    // reinicia al valor configurable
            
                    // Insertar nueva salida random y refrescar tabla
                    fetch("http://127.0.0.1:8000/salidas/random", { method: "POST" })
                    .then(resp => resp.json())
                    .then(() => cargarDatos());
                }
            }


            function cargarDatos() {
                // --- Entradas ---
                fetch("http://127.0.0.1:8000/entradas")
                .then(resp => resp.json())
                .then(data => {
                    // Tomar sólo los últimos 3 registros
                    const ultimos = data.slice(-3);
                    let filas = "";
                    ultimos.forEach(f => {
                        const boolClass = f.n1 ? "bool-true" : "bool-false";
                        const boolText  = f.n1 ? "True" : "False";
                        const colorN2   = getColorForValue(f.n2);

                        filas += `<tr>
                                    <td class="${boolClass}">${boolText}</td>
                                    <td style="background-color:${colorN2}; color:#ffffff; font-weight:bold;">
                                        ${f.n2}
                                    </td>
                                  </tr>`;
                    });
                    document.getElementById("entradas-body").innerHTML = filas;
                });

                // --- Salidas ---
                fetch("http://127.0.0.1:8000/salidas")
                .then(resp => resp.json())
                .then(data => {
                    const ultimos = data.slice(-3);
                    let filas = "";
                    ultimos.forEach(f => {
                        const colorN1 = getColorForValue(f.n1);
                        const colorN2 = getColorForValue(f.n2);

                        filas += `<tr>
                                    <td style="background-color:${colorN1}; color:#ffffff; font-weight:bold;">
                                        ${f.n1}
                                    </td>
                                    <td style="background-color:${colorN2}; color:#ffffff; font-weight:bold;">
                                        ${f.n2}
                                    </td>
                                  </tr>`;
                    });
                    document.getElementById("salidas-body").innerHTML = filas;
                });
            }

            // Primera carga inmediata
            cargarDatos();

            // Contador + inserción + refresco cada 10s
            setInterval(actualizarContador, 1000);
        </script>
            <button onclick="window.location.href='/grafico'"
            style="margin-top:20px; padding:10px 20px; border:none; border-radius:6px;
               background-color:#1abc9c; color:#ffffff; font-size:14px; cursor:pointer;">
               Ver gráfico de salidas
               </button>

        
    </body>
    </html>
    """
    return html



@app.get("/grafico", response_class=HTMLResponse)
def grafico():
    html = """
    <html>
    <head>
        <title>Gráfico de Salidas - Reto Fuzzy</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {
                margin: 0;
                padding: 40px 0;
                font-family: Arial, Helvetica, sans-serif;
                background-color: #232b3b;
                color: #f5f5f5;
                display: flex;
                flex-direction: column;
                align-items: center;
            }
            h1 { font-size: 28px; margin-bottom: 10px; }
            #contador-grafico { font-size: 16px; color: #1abc9c; margin-bottom: 20px; }

            .btn {
                margin: 10px 5px;
                padding: 10px 20px;
                border: none;
                border-radius: 6px;
                background-color: #1abc9c;
                color: #ffffff;
                font-size: 14px;
                cursor: pointer;
            }

            .btn.secundario {
                background-color: #7f8c8d;
            }

            .chart-container {
                background-color: #343f57;
                border-radius: 10px;
                box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5);
                padding: 20px 30px;
                width: 80%;
                max-width: 1000px;
            }

            .chart-controls {
                margin-top: 15px;
                text-align: center;
                font-size: 14px;
            }

            .chart-controls input[type=range] {
                width: 70%;
            }
        </style>
    </head>
    <body>
        <h1>Gráfico de salidas (últimas 50 por ventana)</h1>
        <div id="contador-grafico">Actualización en: <span id="segundos-grafico">10</span>s</div>

        <div>
            <button class="btn" onclick="window.location.href='/panel'">
                Volver al panel principal
            </button>
            <button class="btn" id="btn-play">Play</button>
            <button class="btn secundario" id="btn-pause">Pause</button>
        </div>

        <div class="chart-container">
            <canvas id="salidasChart" height="120"></canvas>

            <div class="chart-controls">
                <label for="window-slider">Navegar histórico (ventana de 50):</label><br>
                <input type="range" id="window-slider" min="0" value="0">
                <div id="window-info"></div>
            </div>
        </div>

        <script>
            let tiempoActual = 10;        // tiempo real desde /config
            let intervalo = 10;           // contador regresivo
            let isRunning = true;         // Play/Pause
            let cachedData = [];          // datos completos de /salidas
            let chart = null;             // gráfico Chart.js
            const ventanaSize = 50;       // tamaño de la ventana mostrada

            // Obtener tiempo_refresco desde el backend
            function cargarConfig() {
                fetch("http://127.0.0.1:8000/config")
                .then(resp => resp.json())
                .then(cfg => {
                    tiempoActual = cfg.tiempo_refresco;
                    intervalo = tiempoActual;
                    document.getElementById("segundos-grafico").innerText = intervalo;
                })
                .catch(err => console.error("Error al cargar config:", err));
            }

            // Renderizar ventana deslizante
            function renderChartWindow(endIndex) {
                const len = cachedData.length;
                if (len === 0) return;

                if (endIndex >= len) endIndex = len - 1;
                if (endIndex < 0) endIndex = 0;

                const startIndex = Math.max(0, endIndex - (ventanaSize - 1));
                const windowData = cachedData.slice(startIndex, endIndex + 1);

                const labels = windowData.map(f => f.id_salidas);
                const valoresN1 = windowData.map(f => f.n1);
                const valoresN2 = windowData.map(f => f.n2);

                const ctx = document.getElementById('salidasChart').getContext('2d');

                if (!chart) {
                    chart = new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [
                                {
                                    label: 'n1',
                                    data: valoresN1,
                                    borderColor: '#1abc9c',
                                    borderWidth: 2,
                                    tension: 0.2
                                },
                                {
                                    label: 'n2',
                                    data: valoresN2,
                                    borderColor: '#e67e22',
                                    borderWidth: 2,
                                    tension: 0.2
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            plugins: {
                                legend: {
                                    labels: { color: '#f5f5f5' }
                                }
                            },
                            scales: {
                                x: {
                                    title: { display: true, text: 'id_salidas', color: '#f5f5f5' },
                                    ticks: { color: '#f5f5f5' },
                                    grid: { color: 'rgba(255,255,255,0.1)' }
                                },
                                y: {
                                    title: { display: true, text: 'Valores', color: '#f5f5f5' },
                                    ticks: { color: '#f5f5f5' },
                                    grid: { color: 'rgba(255,255,255,0.1)' }
                                }
                            }
                        }
                    });
                } else {
                    chart.data.labels = labels;
                    chart.data.datasets[0].data = valoresN1;
                    chart.data.datasets[1].data = valoresN2;
                    chart.update();
                }

                document.getElementById("window-info").innerText =
                    `Mostrando ${startIndex + 1}–${endIndex + 1} de ${len}`;
            }

            // Cargar datos desde backend y actualizar cache + grafico
            function cargarDatosGrafico() {
                fetch("http://127.0.0.1:8000/salidas")
                .then(resp => resp.json())
                .then(data => {
                    cachedData = data;

                    const len = cachedData.length;
                    const slider = document.getElementById("window-slider");

                    if (len > 0) {
                        slider.max = len - 1;
                        const endIndex = parseInt(slider.value) || (len - 1);
                        slider.value = endIndex;
                        renderChartWindow(endIndex);
                    }
                })
                .catch(err => console.error("Error al cargar gráfico:", err));
            }

            // Tick del contador
            function tickGrafico() {
                const contadorEl = document.getElementById("segundos-grafico");

                if (!isRunning) {
                    contadorEl.innerText = "Pausado";
                    return;
                }

                contadorEl.innerText = intervalo;
                intervalo--;

                if (intervalo < 0) {
                    intervalo = tiempoActual;

                    // Insertar un valor nuevo y refrescar gráfico
                    fetch("http://127.0.0.1:8000/salidas/random", { method: "POST" })
                    .then(() => cargarDatosGrafico());
                }
            }

            // Configurar Play / Pause
            function configurarPlayPause() {
                document.getElementById("btn-play").addEventListener("click", () => {
                    isRunning = true;
                    intervalo = tiempoActual;
                });

                document.getElementById("btn-pause").addEventListener("click", () => {
                    isRunning = false;
                });
            }

            // Slider
            function configurarSlider() {
                const slider = document.getElementById("window-slider");
                slider.addEventListener("input", () => {
                    renderChartWindow(parseInt(slider.value));
                });
            }

            // Inicialización
            cargarConfig();
            configurarPlayPause();
            configurarSlider();
            cargarDatosGrafico();
            setInterval(tickGrafico, 1000);
        </script>
    </body>
    </html>
    """
    return html