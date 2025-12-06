from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from opcua import Client
import asyncio

app = FastAPI()

html = """
<!DOCTYPE html>
<html>
<head>
    <title>OPC Monitor</title>
</head>
<body>
<h2>Monitoreo OPC en tiempo real</h2>

<canvas id="chart" width="800" height="350" style="border:1px solid black;"></canvas>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
console.log("Iniciando WebSocket...");

var ws = new WebSocket("ws://localhost:8000/ws");

ws.onopen = function() {
    console.log("WebSocket conectado");
};

ws.onerror = function(e) {
    console.log("Error en WebSocket:", e);
};

var labels = [];
var data_bolas = [];
var data_sag = [];
var data_nivel = [];

var ctx = document.getElementById('chart').getContext('2d');
var chart = new Chart(ctx, {
    type: 'line',
    data: {
        labels: labels,
        datasets: [
            { 
                label: "Potencia Bolas", 
                data: data_bolas, 
                borderWidth: 2, 
                borderColor: "red" 
            },
            { 
                label: "Potencia SAG", 
                data: data_sag, 
                borderWidth: 2, 
                borderColor: "blue" 
            },
            { 
                label: "Nivel Molino", 
                data: data_nivel, 
                borderWidth: 2, 
                borderColor: "green" 
            }
        ]
    },
    options: {
        animation: false,
        responsive: false,
    }
});

ws.onmessage = function(event) {
    var obj = JSON.parse(event.data);
    console.log("Datos recibidos:", obj);

    labels.push("");
    data_bolas.push(obj.bolas);
    data_sag.push(obj.sag);
    data_nivel.push(obj.nivel);

    if (labels.length > 50) {
        labels.shift();
        data_bolas.shift();
        data_sag.shift();
        data_nivel.shift();
    }

    chart.update();
};
</script>
</body>
</html>
"""

@app.get("/")
async def get():
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    client = Client("opc.tcp://localhost:4840")
    client.connect()

    pot_bolas = client.get_node("ns=2;i=2")
    pot_sag   = client.get_node("ns=2;i=3")
    nivel     = client.get_node("ns=2;i=4")

    try:
        while True:
            await asyncio.sleep(1)

            data = {
                "bolas": float(pot_bolas.get_value()),
                "sag":   float(pot_sag.get_value()),
                "nivel": float(nivel.get_value())
            }

            await websocket.send_json(data)

    except Exception as e:
        print("WebSocket cerrado:", e)

    finally:
        client.disconnect()
        print("Cliente OPC desconectado.")