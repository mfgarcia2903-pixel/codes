from fastapi import FastAPI, UploadFile, File, HTTPException
import fitz  # PyMuPDF (Librería ligera y rápida para PDFs)
import re

app = FastAPI()

# ---------------------------------------------------------
# 1. UTILIDADES DE LIMPIEZA Y EXTRACCIÓN
# ---------------------------------------------------------

def detect_unit_multiplier(text):
    """Detecta si los montos están en miles o millones."""
    t = text.lower()
    if "millones de pesos" in t or "millones" in t:
        return 1000.0
    if "miles de pesos" in t or "miles" in t:
        return 1.0
    if re.search(r"\bmillones\b", t):
        return 1000.0
    return 1.0

def clean_number_str(s):
    """Convierte strings financieros (ej: '(1,200)') a números (-1200)."""
    if s is None: return 0
    s = s.strip()
    negative = False
    
    # Manejo de paréntesis para negativos
    if "(" in s and ")" in s:
        negative = True
        s = s.replace("(", "").replace(")", "")
    
    # Limpieza de símbolos
    s = s.replace("$", "").replace("%", "").replace("—", "").replace("–", "").replace("−", "-")
    s = re.sub(r"[A-Za-z]", "", s) # Quitar letras
    s = re.sub(r"[^0-9\-,.\-]", "", s) # Quitar basura
    s = s.replace(",", "") # Quitar comas de miles
    s = s.replace(" ", "")
    
    if s == "": return 0
    
    try:
        val = float(s)
        if negative: val = -abs(val)
        return int(val) # Devolvemos enteros para Firestore
    except:
        return 0

def find_value_after_label(text, label_patterns, multiplier=1.0, prefer_first=True):
    """Busca una etiqueta en el texto y extrae el número más cercano."""
    for pattern in label_patterns:
        regex = re.compile(pattern, flags=re.IGNORECASE)
        matches = list(regex.finditer(text))
        if not matches: continue
        
        # Iteramos sobre las coincidencias encontradas
        for m in matches if prefer_first else reversed(matches):
            start = m.start()
            
            # ESTRATEGIA 1: Buscar en los siguientes 400 caracteres
            snippet = text[start : start + 400]
            # Regex para encontrar números con formato financiero (1,234.56)
            num_match = re.search(r"\(?\d{1,3}(?:[,\.\s]\d{3})*(?:\.\d+)?\)?", snippet)
            
            if num_match:
                val = clean_number_str(num_match.group(0))
                if val != 0: return int(val * multiplier)
            
            # ESTRATEGIA 2 (Fallback): Buscar en la misma línea hacia atrás o adelante
            line_start = text.rfind("\n", 0, start) + 1
            line_end = text.find("\n", start)
            line_end = len(text) if line_end == -1 else line_end
            line = text[line_start:line_end]
            num_match2 = re.search(r"\(?-?\d{1,3}(?:[,\.\s]\d{3})*(?:\.\d+)?\)?", line)
            
            if num_match2:
                val = clean_number_str(num_match2.group(0))
                if val != 0: return int(val * multiplier)
    return 0

# ---------------------------------------------------------
# 2. DICCIONARIOS DE PATRONES REGEX
# ---------------------------------------------------------

bg_patterns = {
    "caja_y_bancos": [r"Efectivo y equivalentes de efectivo", r"Efectivo y equivalentes", r"Efectivo y equivalentes de efectivo al final del periodo"],
    "clientes": [r"Cuentas por cobrar a clientes", r"Cuentas por cobrar", r"CXC", r"Clientes, neto"],
    "impuestos_a_favor": [r"Impuestos por recuperar", r"Impuestos a favor", r"IVA acreditable"],
    "inventario": [r"Almacén de materiales", r"Inventario", r"Almacén", r"Inventarios, neto"],
    "maquinaria_y_equipo": [r"Propiedades, planta y equipo", r"Mejoras a locales arrendados,.*mobiliario y equipo", r"maquinaria, mobiliario y equipo"],
    "total_activo_circulante": [r"Total de activo circulante", r"TOTAL ACTIVO CIRCULANTE", r"Total activos corrientes"],
    "total_activo_fijo": [r"Total de activo no circulante", r"TOTAL ACTIVO FIJO", r"Total activos no corrientes"],
    "total_activo": [r"Total activos", r"Total de activo", r"TOTAL ACTIVO", r"Total de Activos"]
}

pasivo_patterns = {
    "proveedores": [r"Cuentas por pagar a proveedores", r"Proveedores"],
    "impuestos_por_pagar": [r"Impuestos por pagar", r"Impuestos a la utilidad por pagar"],
    "pasivo_financiero_cp": [r"Préstamos bancarios a corto plazo", r"Préstamos", r"Porción circulante de la deuda"],
    "pasivo_financiero_lp": [r"Préstamos bancarios a largo plazo", r"Deuda financiera", r"Deuda a largo plazo"],
    "total_pasivo_no_circulante": [r"Total de pasivo no circulante", r"Total pasivos no corrientes"],
    "total_pasivo_circulante": [r"Total de pasivo circulante", r"Total pasivos corrientes"],
    "total_pasivo": [r"Total de pasivo", r"Total Pasivos"]
}

er_patterns = {
    "ventas": [r"Ingresos Totales", r"Ventas netas", r"Ingresos por ventas"],
    "costo_de_ventas": [r"Costo de ventas", r"Costo de lo vendido", r"Gastos de Operación"], # Ajustar según sector
    "utilidad_neta": [r"Utilidad del ejercicio", r"Utilidad neta", r"Resultado del Ejercicio", r"Utilidad \(pérdida\) neta"]
}

# ---------------------------------------------------------
# 3. LÓGICA PRINCIPAL
# ---------------------------------------------------------

def process_pdf_bytes(file_bytes):
    # Abrimos el PDF desde memoria
    text = ""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text() + "\n"

    multiplier = detect_unit_multiplier(text)
    
    # Helper local
    def get_val(patterns, default=0):
        v = find_value_after_label(text, patterns, multiplier)
        return int(v) if v != 0 else default

    # --- EXTRACCIÓN BALANCE GENERAL ---
    caja = get_val(bg_patterns["caja_y_bancos"])
    clientes = get_val(bg_patterns["clientes"])
    inventario = get_val(bg_patterns["inventario"])
    impuestos_favor = get_val(bg_patterns["impuestos_a_favor"])
    
    total_activo_circ = get_val(bg_patterns["total_activo_circulante"])
    if total_activo_circ == 0: total_activo_circ = caja + clientes + inventario + impuestos_favor # Fallback calculado

    total_activo_fijo = get_val(bg_patterns["total_activo_fijo"])
    total_activo = get_val(bg_patterns["total_activo"])
    if total_activo == 0: total_activo = total_activo_circ + total_activo_fijo

    proveedores = get_val(pasivo_patterns["proveedores"])
    impuestos_pagar = get_val(pasivo_patterns["impuestos_por_pagar"])
    pasivo_cp = get_val(pasivo_patterns["pasivo_financiero_cp"])
    
    total_pasivo_circ = get_val(pasivo_patterns["total_pasivo_circulante"])
    if total_pasivo_circ == 0: total_pasivo_circ = proveedores + impuestos_pagar + pasivo_cp

    pasivo_lp = get_val(pasivo_patterns["pasivo_financiero_lp"])
    total_pasivo_no_circ = get_val(pasivo_patterns["total_pasivo_no_circulante"])
    if total_pasivo_no_circ == 0: total_pasivo_no_circ = pasivo_lp

    total_pasivo = get_val(pasivo_patterns["total_pasivo"])
    if total_pasivo == 0: total_pasivo = total_pasivo_circ + total_pasivo_no_circ

    # Capital (Patrimonio)
    capital_social = find_value_after_label(text, [r"Capital social"], multiplier)
    util_retenidas = find_value_after_label(text, [r"Utilidades Retenidas", r"Resultados acumulados"], multiplier)
    
    # --- EXTRACCIÓN ESTADO DE RESULTADOS ---
    ventas = get_val(er_patterns["ventas"])
    costo_ventas = get_val(er_patterns["costo_de_ventas"])
    utilidad_neta = get_val(er_patterns["utilidad_neta"])
    
    # Cálculos derivados básicos
    utilidad_bruta = ventas - costo_ventas
    
    # Ajuste de utilidades retenidas (restando la utilidad del ejercicio actual si aplica)
    # Nota: Esto es una simplificación contable para el modelo
    util_retenidas_adj = util_retenidas - utilidad_neta 
    total_capital = capital_social + util_retenidas_adj + utilidad_neta
    
    # Si no leímos total_activo, lo forzamos por ecuación contable
    if total_activo == 0 and (total_pasivo + total_capital) > 0:
        total_activo = total_pasivo + total_capital

    # -----------------------------------------------------------
    # ESTRUCTURA DE RETORNO GENÉRICA (SIN NOMBRES HARDCODEADOS)
    # -----------------------------------------------------------
    return {
        "Balance General": {
            "activo_total": {
                "activo_circulante": {
                    "caja_y_bancos": int(caja),
                    "clientes": int(clientes),
                    "inventario": int(inventario),
                    "impuestos_a_favor": int(impuestos_favor),
                    "total_activo_circulante": int(total_activo_circ)
                },
                "activo_fijo": {
                    "total_activo_fijo": int(total_activo_fijo)
                },
                "total_activo": int(total_activo)
            },
            "pasivo_total": {
                "pasivo_corto_plazo": {
                    "proveedores": int(proveedores),
                    "impuestos_por_pagar": int(impuestos_pagar),
                    "pasivo_financiero_cp": int(pasivo_cp),
                    "total_pasivo_corto_plazo": int(total_pasivo_circ)
                },
                "pasivo_largo_plazo": {
                    "pasivo_financiero_lp": int(pasivo_lp),
                    "total_pasivo_no_circulante": int(total_pasivo_no_circ)
                },
                "total_pasivo": int(total_pasivo)
            },
            "capital_contable": {
                "capital_social": int(capital_social),
                "utilidades_retenidas_o_acumuladas": int(util_retenidas_adj),
                "utilidad_del_ejercicio": int(utilidad_neta),
                "total_capital": int(total_capital) 
            }
        },
        "Estado de Resultados": {
            "ventas": int(ventas),
            "costo_de_ventas": int(costo_ventas),
            "utilidad_bruta": int(utilidad_bruta),
            "utilidad_neta": int(utilidad_neta)
        }
    }

# ---------------------------------------------------------
# 4. ENDPOINTS
# ---------------------------------------------------------

@app.get("/")
def home():
    return {"status": "online", "message": "API Extracción Financiera Genérica v4"}

@app.post("/extract-financial")
async def extract_text(file: UploadFile = File(...)):
    try:
        # Leemos el archivo completo
        content = await file.read()
        
        # Procesamos
        json_data = process_pdf_bytes(content)
        
        return json_data
        
    except Exception as e:
        print(f"Error procesando archivo: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
