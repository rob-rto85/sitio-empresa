import os
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from flask import Flask, request, redirect
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.pdfgen import canvas

#-------------------------------------------------------------------------------------------
# Inicializamos Flask indicándole que la carpeta raíz contiene los archivos HTML estáticos
app = Flask(__name__, static_folder='.', static_url_path='')
#-------------------------------------------------------------------------------------------

#******************************************************************************************************
# 1. Configuración de la base de datos (Usa SQLite local; en producción Azure se puede cambiar a MySQL)
#******************************************************************************************************
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'db_imprenta_siva.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

#***************************************************
# 2. Modelado de la tabla de Pedidos / Cotizaciones
#***************************************************
class Cotizacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    servicio = db.Column(db.String(100), nullable=False)
    mensaje = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.String(50), default=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

#-------------------------------------------------------------------------------
# Ejecución automática: Crea el archivo de base de datos al arrancar el programa
#-------------------------------------------------------------------------------
with app.app_context():
    db.create_all()

#----------------------------------------------------------------------------------
# Ventana de tiempo (en segundos) para considerar un envío como doble clic/duplicado
#----------------------------------------------------------------------------------
VENTANA_DUPLICADOS_SEGUNDOS = 15

#*********************************************************
# 3. Ruta principal para servir tus páginas HTML estáticas
#*********************************************************
@app.route('/')
def index():
    return app.send_static_file('index.html')

#****************************************************************************
# 4. RUTA BACKEND: El motor que procesa el formulario, genera el PDF y redirige a WhatsApp
#****************************************************************************
@app.route('/procesar-cotizacion', methods=['POST'])
def procesar_cotizacion():

    #-----------------------------------------------------
    # Capturamos los datos enviados desde el formulario
    #-----------------------------------------------------
    nombre = request.form.get('nombre', '').strip()
    email = request.form.get('email', '').strip()
    servicio = request.form.get('servicio', '').strip()
    mensaje = request.form.get('mensaje', '').strip()

    # Validación básica: si falta algún campo, no continuamos
    if not nombre or not email or not servicio or not mensaje:
        return "Faltan datos obligatorios en el formulario.", 400

    # Preparamos de una vez el link de WhatsApp, porque lo necesitamos tanto en el
    # flujo normal como en el flujo de "pedido duplicado" (doble clic del cliente)
    telefono_asesor = "593989125300"  # <--- Formato internacional de Ecuador sin el "+"
    mensaje_wasap = (
        f"Hola Imprenta SIVA, mi nombre es *{nombre}*. Acabo de ingresar una cotizacion web "
        f"para el servicio de *{servicio}*.\n\n*Detalles del pedido:* {mensaje}\n\n"
        f"_(El sistema ha generado mi recibo PDF)_"
    )
    url_whatsapp = f"https://wa.me/{telefono_asesor}?text={quote(mensaje_wasap)}"

    #-----------------------------------------------------------------------------------
    # PROTECCIÓN CONTRA DOBLE ENVÍO (doble clic en "COTIZAR SOLICITUD")
    # Si ya existe un pedido IDÉNTICO (mismos datos) hecho hace muy pocos segundos,
    # asumimos que es el mismo clic duplicado: no creamos otro registro ni otro PDF,
    # simplemente reenviamos al cliente a WhatsApp con el mismo mensaje de siempre.
    #-----------------------------------------------------------------------------------
    limite_tiempo = (datetime.now() - timedelta(seconds=VENTANA_DUPLICADOS_SEGUNDOS)).strftime("%Y-%m-%d %H:%M:%S")
    pedido_duplicado = Cotizacion.query.filter(
        Cotizacion.nombre == nombre,
        Cotizacion.email == email,
        Cotizacion.servicio == servicio,
        Cotizacion.mensaje == mensaje,
        Cotizacion.fecha >= limite_tiempo
    ).first()

    if pedido_duplicado:
        return redirect(url_whatsapp)

    #-----------------------------------------------------
    # REGISTRO EN BASE DE DATOS: Guardamos el registro en la base de datos de pedidos
    #-----------------------------------------------------
    nueva_cotizacion = Cotizacion(nombre=nombre, email=email, servicio=servicio, mensaje=mensaje)
    db.session.add(nueva_cotizacion)
    db.session.commit()

    #----------------------------------------------------------------
    #--- FLUJO A: GENERACIÓN DE RECIBO EN PDF (Librería ReportLab) ---
    #----------------------------------------------------------------

    # Creamos la subcarpeta de comprobantes en assets/ si no existe
    comprobantes_dir = os.path.join(BASE_DIR, 'assets', 'comprobantes')
    os.makedirs(comprobantes_dir, exist_ok=True)

    nombre_pdf = f"cotizacion_{int(time.time())}.pdf"
    ruta_final_pdf = os.path.join(comprobantes_dir, nombre_pdf)

    # Número de folio: usamos el ID autoincremental de la base de datos, así cada
    # cotización queda con un número único y consecutivo (0001, 0002, 0003...)
    folio = f"N° {nueva_cotizacion.id:04d}"

    # Inicializamos el lienzo para el PDF en tamaño carta
    c = canvas.Canvas(ruta_final_pdf, pagesize=letter)
    c.setTitle(f"COTIZACIÓN - IMPRENTA SIVA - {nombre} - {folio}")

    # Dibujamos el encabezado color Azul Marino Corporativo de Imprenta Siva (#0B1B3D)
    # (Corregido: antes el rectángulo iba de 720 a 800, pero una hoja carta solo mide
    # 792pt de alto, así que 8pt del encabezado quedaban recortados fuera de la página.
    # Ahora va de 712 a 792, pegado exactamente al borde superior.)
    c.setFillColor(colors.HexColor('#0B1B3D'))
    c.rect(0, 712, 612, 80, fill=True, stroke=False)

    # Logo de la empresa a la izquierda del encabezado (si el archivo existe)
    ruta_logo = os.path.join(BASE_DIR, 'assets', 'img', 'logo-imprenta.png')
    if os.path.isfile(ruta_logo):
        try:
            c.drawImage(ruta_logo, 40, 727, width=50, height=50,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            # Si el archivo existe pero está dañado o en un formato no soportado,
            # seguimos generando el resto del PDF en vez de romper la cotización
            pass

    # Texto del encabezado en color blanco
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(306, 750, "COTIZACIÓN - IMPRENTA SIVA")

    #-----------------------------------------------------------------------------------
    # RECUADRO-TICKET DE SOLICITUD: en la zona blanca, esquina superior derecha.
    # Borde negro con efecto biselado (doble línea), cintillo azul marino arriba con el
    # título "SOLICITUD" en blanco, y el número de folio en rojo debajo.
    #-----------------------------------------------------------------------------------
    ticket_w, ticket_h = 120, 55
    ticket_x1, ticket_y1 = 565, 700
    ticket_x0, ticket_y0 = ticket_x1 - ticket_w, ticket_y1 - ticket_h

    # Borde exterior negro (más grueso) + borde interior gris claro (más fino),
    # la combinación de las dos líneas es lo que simula el efecto biselado/en relieve
    c.setStrokeColor(colors.black)
    c.setLineWidth(2)
    c.rect(ticket_x0, ticket_y0, ticket_w, ticket_h, fill=False, stroke=True)
    c.setStrokeColor(colors.HexColor('#B0B0B0'))
    c.setLineWidth(1)
    c.rect(ticket_x0 + 3, ticket_y0 + 3, ticket_w - 6, ticket_h - 6, fill=False, stroke=True)

    # Cintillo azul marino con el título "SOLICITUD" en blanco
    cintillo_h = 20
    cintillo_y0 = ticket_y1 - 3 - cintillo_h
    c.setFillColor(colors.HexColor('#0B1B3D'))
    c.rect(ticket_x0 + 3, cintillo_y0, ticket_w - 6, cintillo_h, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString((ticket_x0 + ticket_x1) / 2, cintillo_y0 + 5, "SOLICITUD")

    # Número de folio en rojo, debajo del cintillo
    c.setFillColor(colors.HexColor('#E63946'))
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString((ticket_x0 + ticket_x1) / 2, ticket_y0 + 15, folio)

    #-----------------------------------------------------------------------------------
    # Función auxiliar: envuelve un texto en varias líneas si supera un ancho máximo
    # (en puntos), usando el ancho REAL de cada palabra en la fuente activa, no un
    # conteo de caracteres — así funciona igual de bien con nombres cortos o larguísimos.
    #-----------------------------------------------------------------------------------
    def envolver_texto(texto, fuente, tamano, ancho_max):
        palabras = texto.split()
        lineas = []
        linea_actual = ""
        for palabra in palabras:
            prueba = (linea_actual + " " + palabra).strip()
            if c.stringWidth(prueba, fuente, tamano) <= ancho_max or not linea_actual:
                linea_actual = prueba
            else:
                lineas.append(linea_actual)
                linea_actual = palabra
        if linea_actual:
            lineas.append(linea_actual)
        return lineas

    # Dibujamos los datos del cliente en color negro
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 12)
    c.drawString(50, 660, "Datos del Cliente:")

    c.setFont("Helvetica", 11)
    # El nombre del cliente puede ser muy largo: lo envolvemos en varias líneas dentro
    # de un ancho seguro (330pt) para que nunca choque con el recuadro "SOLICITUD"
    # ni se salga del margen derecho de la hoja.
    y_cursor = 640
    for linea in envolver_texto(f"Cliente: {nombre}", "Helvetica", 11, 330):
        c.drawString(50, y_cursor, linea)
        y_cursor -= 15

    y_cursor -= 5
    c.drawString(50, y_cursor, f"Correo: {email}")
    y_cursor -= 20
    c.drawString(50, y_cursor, f"Servicio requerido: {servicio.upper()}")

    # Recuadro "DETALLE" con letra blanca sobre fondo azul marino, igual estilo que el encabezado
    c.setFillColor(colors.HexColor('#0B1B3D'))
    c.rect(50, 543, 140, 23, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(60, 550, "D E T A L L E")

    #-----------------------------------------------------------------------------------
    # Preparamos las líneas del detalle RESPETANDO los saltos de línea (Enter) que el
    # cliente ya escribió en el formulario — antes usábamos mensaje.split(), que junta
    # TODO el texto sin importar los Enter, aplastando pedidos separados en un solo
    # párrafo corrido. Ahora solo partimos (envolvemos) una línea si de verdad no cabe
    # en el ancho del recuadro; si ya cabe, se respeta tal cual la escribió el cliente.
    #-----------------------------------------------------------------------------------
    ancho_detalle = 512 - 20  # ancho interior del recuadro violeta (con margen a los lados)
    lineas_detalle = []
    for linea_original in mensaje.splitlines():
        if linea_original.strip() == "":
            lineas_detalle.append("")  # conserva líneas en blanco que el cliente haya dejado
        else:
            lineas_detalle.extend(envolver_texto(linea_original, "Helvetica", 11, ancho_detalle))

    interlineado_detalle = 16

    #-----------------------------------------------------------------------------------
    # Funciones auxiliares para el pie de página y el encabezado de continuación,
    # porque ahora se pueden necesitar en más de una página.
    #-----------------------------------------------------------------------------------
    def dibujar_pie_pagina(pie_y0):
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor('#4A5568'))
        c.drawCentredString(306, pie_y0, "AV. Jimmy Anchico y Anne Orejuela, Quinindé - Esmeraldas, Ecuador")
        c.drawCentredString(306, pie_y0 - 13, "WhatsApp: 098 912 5300 / 099 800 2439 / 099 768 9211")
        c.drawCentredString(306, pie_y0 - 26, "Email: impre_siva@yahoo.com")

        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(colors.gray)
        c.drawCentredString(306, pie_y0 - 55, "Este documento certifica que su requerimiento esta ingresado en nuestro sistema.")
        c.drawCentredString(306, pie_y0 - 70, "Imprenta SIVA - Quinindé, Ecuador - 50 Años a su servicio.")

    def dibujar_encabezado_continuacion():
        # Encabezado simplificado para las páginas 2, 3, etc: mantiene el mismo folio
        # visible para que quede claro que es la misma orden, solo que continúa.
        c.setFillColor(colors.HexColor('#0B1B3D'))
        c.rect(0, 742, 612, 50, fill=True, stroke=False)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(50, 760, "COTIZACIÓN - IMPRENTA SIVA (continuación)")
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(562, 760, folio)

    #-----------------------------------------------------------------------------------
    # PAGINACIÓN DEL DETALLE: si las líneas no caben en una sola hoja, seguimos en una
    # página nueva (repitiendo el folio en un encabezado simplificado, misma orden), y
    # el pie de página con los datos de contacto solo se dibuja al final, en la última
    # hoja — nunca en medio del detalle.
    #-----------------------------------------------------------------------------------
    ESPACIO_RESERVADO_PIE = 150  # espacio que siempre dejamos libre abajo para el pie de página
    y_top_caja = 530             # techo del recuadro en la página 1, justo bajo "DETALLE"
    y_top_caja_continuacion = 715  # techo del recuadro en páginas 2, 3... (bajo el encabezado simplificado)

    lineas_restantes = list(lineas_detalle)
    numero_pagina = 1

    while True:
        techo_actual = y_top_caja if numero_pagina == 1 else y_top_caja_continuacion
        capacidad_pagina = (techo_actual - ESPACIO_RESERVADO_PIE - 30) // interlineado_detalle

        chunk = lineas_restantes[:capacidad_pagina]
        lineas_restantes = lineas_restantes[capacidad_pagina:]
        es_ultima_pagina = not lineas_restantes

        # Alto del recuadro: si el chunk es corto, el recuadro se ajusta a su contenido
        # (mínimo 130pt, igual que antes); si es la página que se queda sin espacio,
        # ocupa el máximo disponible antes del área reservada para el pie de página.
        alto_necesario = len(chunk) * interlineado_detalle + 30
        alto_caja = max(130, alto_necesario)
        y0_caja = techo_actual - alto_caja

        c.setStrokeColor(colors.HexColor("#560BAD"))
        c.rect(50, y0_caja, 512, alto_caja, fill=False, stroke=True)

        c.setFillColor(colors.black)
        c.setFont("Helvetica", 11)
        textobject = c.beginText(60, techo_actual - 20)
        textobject.setLeading(interlineado_detalle)
        for linea in chunk:
            textobject.textLine(linea)
        c.drawText(textobject)

        if es_ultima_pagina:
            # Pie de página SOLO en la última hoja, calculado a partir de dónde terminó
            # el recuadro (para que nunca quede pegado ni se le encime)
            pie_y0 = min(110, y0_caja - 30)
            dibujar_pie_pagina(pie_y0)
            break
        else:
            # Aviso de que el detalle continúa, y saltamos a una página nueva
            c.setFont("Helvetica-Oblique", 9)
            c.setFillColor(colors.gray)
            c.drawCentredString(306, y0_caja - 15, "(continúa en la página siguiente)")

            c.showPage()  # cierra la página actual y abre una nueva en blanco
            numero_pagina += 1
            dibujar_encabezado_continuacion()

    #-----------------------------------------------
    # Guardamos y cerramos el archivo PDF físicamente
    #-----------------------------------------------
    c.save()

    #--------------------------------------------------------------------
    #  --- FLUJO B: Redirigimos al cliente directamente al WhatsApp del asesor comercial ---
    #--------------------------------------------------------------------
    return redirect(url_whatsapp)


if __name__ == '__main__':
    # Azure App Service define la variable de entorno PORT; en tu máquina local
    # esa variable no existe, así que usamos 5000 como valor por defecto.
    puerto = int(os.environ.get('PORT', 5000))

    # Detectamos el entorno automáticamente: si existe PORT, asumimos que estamos en
    # Azure (producción) y apagamos el modo debug (no debe quedar activo en producción,
    # expone información sensible). En tu máquina local, sin esa variable, el modo
    # debug se mantiene encendido como hasta ahora, para que sigas viendo errores
    # detallados mientras desarrollas.
    modo_debug = 'PORT' not in os.environ

    # Agregamos prints explícitos para ver el arranque pase lo que pase en la terminal
    print("\n==================================================")
    print("  🚀 ¡SERVIDOR DE IMPRENTA SIVA DESPIERTO!")
    print(f"  🔗 Abre en tu navegador: http://localhost:{puerto}")
    print("==================================================\n")

    app.run(host='0.0.0.0', port=puerto, debug=modo_debug, use_reloader=False)
