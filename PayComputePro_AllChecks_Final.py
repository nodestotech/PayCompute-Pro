import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import io
import json

# ============================================================================
# CONFIGURATION
# ============================================================================

CURRENCY = "AED"
MIN_MONTH = 1
MAX_MONTH = 12
MIN_YEAR = 2020
MAX_YEAR = 2050
AMOUNT_WARNING_THRESHOLD = 10000
AUDIT_LOG_FILE = "download_audit.json"

# ============================================================================
# PAGE CONFIG
# ============================================================================

st.set_page_config(
    page_title="PayCompute Pro - SF Payroll Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================================
# STYLING & BRANDING
# ============================================================================

st.markdown("""
    <style>
    .branding-title {
        font-size: 32px;
        font-weight: bold;
        color: #1f77b4;
        margin: 0;
        padding: 0;
    }
    .branding-subtitle {
        font-size: 11px;
        color: #666666;
        margin: 2px 0 0 0;
        padding: 0;
        font-weight: normal;
    }
    .check-passed {
        color: #28a745;
        font-weight: bold;
    }
    .check-warning {
        color: #ffc107;
        font-weight: bold;
    }
    .check-failed {
        color: #dc3545;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# QUALITY CONTROL CHECKS
# ============================================================================

def check_1_file_structure_validation(payroll_df):
    """CHECK 1: File Structure Validation"""
    errors = []
    warnings = []
    
    # Check minimum rows
    if len(payroll_df) < 3:
        errors.append("❌ File has less than 3 rows. Need: Row 1 (codes), Row 2 (data start)")
    
    # Check Row 1 has deduction codes
    row1_values = payroll_df.iloc[0].dropna()
    if len(row1_values) < 2:
        errors.append("❌ Row 1 appears empty. Should contain deduction codes")
    
    # Check Column A (Staff IDs)
    col_a = payroll_df.iloc[1:, 0].dropna()
    if len(col_a) == 0:
        errors.append("❌ Column A (Staff IDs) appears empty")
    
    return errors, warnings

def check_2_staff_id_validation(payroll_df):
    """CHECK 2: Staff ID Validation"""
    errors = []
    warnings = []
    
    valid_ids = 0
    invalid_ids = []
    blank_ids = 0
    
    for idx in range(2, len(payroll_df)):
        emp_id = str(payroll_df.iloc[idx, 0]).strip()
        
        if not emp_id or emp_id.lower() == 'nan' or emp_id == '':
            blank_ids += 1
            invalid_ids.append(f"Row {idx+1}: Blank Staff ID")
        elif len(emp_id) < 5:
            invalid_ids.append(f"Row {idx+1}: ID '{emp_id}' seems too short")
        else:
            valid_ids += 1
    
    total = valid_ids + blank_ids + len([x for x in invalid_ids if 'too short' in x])
    
    if total == 0:
        errors.append("❌ No valid rows found after header")
    elif blank_ids > 0:
        warnings.append(f"⚠️ Found {blank_ids} rows with blank Staff IDs")
    
    if len(invalid_ids) > 0 and len(invalid_ids) <= 5:
        for err in invalid_ids[:5]:
            warnings.append(f"⚠️ {err}")
    
    return errors, warnings, valid_ids, blank_ids

def check_3_wage_code_validation(payroll_df, wage_mapping):
    """CHECK 3: Wage Code Validation"""
    errors = []
    warnings = []
    
    headers = payroll_df.iloc[0]
    unmapped_codes = []
    mapped_codes = 0
    
    for col_idx in range(len(headers)):
        header_val = str(headers.iloc[col_idx]).strip().upper()
        
        if pd.isna(header_val) or header_val == 'NAN' or header_val == '':
            continue
        
        if header_val in wage_mapping:
            mapped_codes += 1
        elif header_val not in ['DEDUCTIONS', 'STAFF ID', 'ROW LABELS', 'STORE', 'DESIGNATION', 'GRAND TOTAL']:
            unmapped_codes.append(header_val)
    
    if unmapped_codes:
        if len(unmapped_codes) <= 5:
            for code in unmapped_codes:
                warnings.append(f"⚠️ Code '{code}' not in wage mapping (may be data column)")
        else:
            warnings.append(f"⚠️ Found {len(unmapped_codes)} unmapped codes (showing first 5)")
            for code in unmapped_codes[:5]:
                warnings.append(f"   - '{code}'")
    
    return errors, warnings, mapped_codes, len(unmapped_codes)

def check_4_amount_range_validation(payroll_df, wage_mapping):
    """CHECK 4: Amount Range Validation"""
    errors = []
    warnings = []
    
    headers = payroll_df.iloc[0]
    suspicious_amounts = []
    negative_amounts = []
    
    for col_idx in range(len(headers)):
        header_val = str(headers.iloc[col_idx]).strip().upper()
        if header_val not in wage_mapping:
            continue
        
        for row_idx in range(2, len(payroll_df)):
            try:
                amount = float(payroll_df.iloc[row_idx, col_idx])
                
                if amount < 0:
                    emp_id = str(payroll_df.iloc[row_idx, 0])
                    negative_amounts.append(f"Row {row_idx+1}, Emp {emp_id}: Negative amount {amount}")
                elif amount > AMOUNT_WARNING_THRESHOLD:
                    emp_id = str(payroll_df.iloc[row_idx, 0])
                    suspicious_amounts.append(f"Row {row_idx+1}, Emp {emp_id}: High amount {amount:.2f}")
            except:
                pass
    
    if negative_amounts:
        errors.extend([f"❌ {x}" for x in negative_amounts[:3]])
        if len(negative_amounts) > 3:
            errors.append(f"❌ ... and {len(negative_amounts)-3} more negative amounts")
    
    if suspicious_amounts:
        for amt in suspicious_amounts[:3]:
            warnings.append(f"⚠️ Unusual: {amt} AED (> {AMOUNT_WARNING_THRESHOLD})")
        if len(suspicious_amounts) > 3:
            warnings.append(f"⚠️ ... and {len(suspicious_amounts)-3} more high amounts")
    
    return errors, warnings

def check_6_data_quality_report(deductions):
    """CHECK 6: Data Quality Report"""
    report = {}
    
    if not deductions:
        return report
    
    unique_employees = len(set(d['emp_id'] for d in deductions))
    unique_components = len(set(d['component'] for d in deductions))
    total_amount = sum(d['amount'] for d in deductions)
    avg_amount = total_amount / len(deductions) if deductions else 0
    
    report['total_records'] = len(deductions)
    report['unique_employees'] = unique_employees
    report['unique_components'] = unique_components
    report['total_amount'] = total_amount
    report['avg_amount'] = avg_amount
    report['min_amount'] = min(d['amount'] for d in deductions) if deductions else 0
    report['max_amount'] = max(d['amount'] for d in deductions) if deductions else 0
    
    return report

def check_7_missing_blank_cells(payroll_df, wage_mapping):
    """CHECK 7: Missing/Blank Cell Detection"""
    warnings = []
    
    headers = payroll_df.iloc[0]
    blank_cell_rows = []
    
    for col_idx in range(len(headers)):
        header_val = str(headers.iloc[col_idx]).strip().upper()
        if header_val not in wage_mapping:
            continue
        
        for row_idx in range(2, len(payroll_df)):
            cell_value = payroll_df.iloc[row_idx, col_idx]
            if pd.isna(cell_value) or str(cell_value).strip() == '':
                emp_id = str(payroll_df.iloc[row_idx, 0])
                blank_cell_rows.append(f"Row {row_idx+1}, Emp {emp_id}: Blank cell")
    
    if blank_cell_rows:
        warnings.extend([f"⚠️ {x}" for x in blank_cell_rows[:3]])
        if len(blank_cell_rows) > 3:
            warnings.append(f"⚠️ ... and {len(blank_cell_rows)-3} more blank cells")
    
    return warnings

def check_8_component_consistency(deductions):
    """CHECK 8: Component Consistency Check"""
    warnings = []
    
    # Check for case inconsistencies
    components = {}
    for ded in deductions:
        component_upper = ded['component'].upper()
        if component_upper not in components:
            components[component_upper] = ded['component']
        elif components[component_upper] != ded['component']:
            warnings.append(f"⚠️ Component case inconsistency: '{components[component_upper]}' vs '{ded['component']}'")
    
    return warnings

def check_9_pre_download_validation(csv_content):
    """CHECK 9: Pre-Download Validation"""
    errors = []
    warnings = []
    
    lines = csv_content.strip().split('\n')
    
    # Check Row 1: System headers
    expected_row1 = 'currency-code,pay-date,pay-component-code,user-id,value,operation'
    if lines[0] != expected_row1:
        errors.append("❌ Row 1: System headers incorrect")
    
    # Check Row 2: Display headers
    expected_row2 = 'Currency,Issue Date,Pay Component,User ID,Spot Bonus Amount,Operation'
    if lines[1] != expected_row2:
        errors.append("❌ Row 2: Display headers incorrect")
    
    # Check for zero amounts in data
    zero_count = 0
    for line in lines[2:]:
        if line.strip():
            parts = line.split(',')
            if len(parts) > 4:
                try:
                    if float(parts[4]) == 0:
                        zero_count += 1
                except:
                    pass
    
    if zero_count > 0:
        errors.append(f"❌ Found {zero_count} records with 0 amount (should be filtered)")
    
    # Check file structure
    if len(lines) < 3:
        errors.append("❌ CSV has less than 3 rows (need headers + data)")
    
    return errors, warnings

def check_10_audit_log(sheet_name, record_count, total_amount):
    """CHECK 10: Download Audit Log"""
    audit_entry = {
        'timestamp': datetime.now().isoformat(),
        'sheet_name': sheet_name,
        'record_count': record_count,
        'total_amount': round(total_amount, 2),
        'filename': f"{sheet_name}.csv"
    }
    
    try:
        if Path(AUDIT_LOG_FILE).exists():
            with open(AUDIT_LOG_FILE, 'r') as f:
                audit_log = json.load(f)
        else:
            audit_log = []
        
        audit_log.append(audit_entry)
        
        with open(AUDIT_LOG_FILE, 'w') as f:
            json.dump(audit_log, f, indent=2)
    except:
        pass
    
    return audit_entry

# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def get_days_in_month(month, year):
    if month == 2:
        return 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
    elif month in [4, 6, 9, 11]:
        return 30
    else:
        return 31

def load_wage_mapping(mapping_file):
    try:
        df = pd.read_excel(mapping_file, sheet_name=0, header=None)
        mapping = {}
        for idx, row in df.iterrows():
            if pd.notna(row[0]) and pd.notna(row[1]):
                code = str(row[0]).strip().upper()
                component = str(row[1]).strip()
                if code not in ['RAMCO CODE', 'NOT AVAILABLE'] and component not in ['SAP PAYCOMPONENT', 'NOT AVAILABLE']:
                    mapping[code] = component
        return mapping
    except Exception as e:
        st.error(f"Error loading wage mapping: {str(e)}")
        return None

def extract_deductions_row1(payroll_df, wage_mapping):
    deductions = []
    
    try:
        headers = payroll_df.iloc[0]
        deduction_cols = {}
        found_codes = []
        
        for col_idx in range(len(headers)):
            header_val = str(headers.iloc[col_idx]).strip().upper()
            
            if pd.isna(header_val) or header_val == 'NAN' or header_val == '':
                continue
            
            if header_val in wage_mapping:
                deduction_cols[col_idx] = (header_val, wage_mapping[header_val])
                found_codes.append(header_val)
        
        if not deduction_cols:
            return None, f"❌ No deduction codes found in Row 1."
        
        for row_idx in range(2, len(payroll_df)):
            row = payroll_df.iloc[row_idx]
            
            try:
                emp_id = str(row.iloc[0]).strip()
                emp_name = str(row.iloc[1]).strip() if len(row) > 1 else "Unknown"
                
                if not emp_id or emp_id.lower() == 'nan' or emp_id == '':
                    continue
                
                for col_idx, (ramco_code, component) in deduction_cols.items():
                    try:
                        amount = float(row.iloc[col_idx]) if col_idx < len(row) else 0
                        
                        if pd.notna(amount) and amount > 0:
                            deductions.append({
                                'emp_id': emp_id,
                                'emp_name': emp_name,
                                'code': ramco_code,
                                'component': component,
                                'amount': amount
                            })
                    except (ValueError, TypeError):
                        continue
            except Exception as e:
                continue
        
        return deductions, None
    
    except Exception as e:
        return None, f"❌ Error extracting deductions: {str(e)}"

def rotate_dates_descending(deductions, month, year):
    days_in_month = get_days_in_month(month, year)
    date_tracker = defaultdict(int)
    
    for deduction in deductions:
        key = (deduction['emp_id'], deduction['component'])
        day = days_in_month - date_tracker[key]
        
        if day < 1:
            day = 1
        
        deduction['pay_date'] = f"{day:02d}/{month:02d}/{year}"
        date_tracker[key] += 1
    
    return deductions

def generate_csv_with_two_header_rows(deductions):
    row1_headers = ['currency-code', 'pay-date', 'pay-component-code', 'user-id', 'value', 'operation']
    row2_headers = ['Currency', 'Issue Date', 'Pay Component', 'User ID', 'Spot Bonus Amount', 'Operation']
    
    output = io.StringIO()
    output.write(','.join(row1_headers) + '\n')
    output.write(','.join(row2_headers) + '\n')
    
    for ded in deductions:
        if ded['amount'] > 0:
            row_data = [
                CURRENCY,
                ded['pay_date'],
                str(ded['component']),
                str(ded['emp_id']),
                str(round(float(ded['amount']), 2)),
                ''
            ]
            output.write(','.join(row_data) + '\n')
    
    return output.getvalue()

# ============================================================================
# MAIN APP
# ============================================================================

col1, col2 = st.columns([0.5, 0.5])

with col1:
    st.markdown("""
    <div class="branding-title">PayCompute Pro</div>
    <div class="branding-subtitle">Built by Ananth</div>
    """, unsafe_allow_html=True)

with col2:
    with st.expander("ℹ️ How It Works"):
        st.markdown("""
        **Features:**
        - Smart data validation
        - Quality control checks
        - Error detection
        - Data quality reports
        - Audit logging
        """)

st.divider()

with st.sidebar:
    st.markdown("""
    <div class="branding-title" style="font-size: 24px;">PayCompute Pro</div>
    <div class="branding-subtitle" style="font-size: 10px;">Built by Ananth</div>
    """, unsafe_allow_html=True)
    
    st.divider()
    st.markdown("## ⚙️ Configuration")
    
    uploaded_file = st.file_uploader("📤 Upload Payroll File", type=["xlsx", "xls"])
    
    if uploaded_file:
        st.success(f"✅ {uploaded_file.name}")
    
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        month = st.selectbox("Month", range(1, 13), index=8)
    with col2:
        year = st.selectbox("Year", range(2020, 2051), index=5)
    
    st.divider()
    
    st.markdown("### 📄 Output Sheet Name")
    st.info("File name will match this sheet name")
    sheet_name = st.text_input("Sheet/File Name", value="1")
    
    st.divider()
    
    if st.button("🚀 Generate SF Upload", use_container_width=True, type="primary"):
        if not uploaded_file:
            st.error("❌ Upload file first")
        else:
            st.session_state.generate = True

if not uploaded_file:
    st.info("👈 Upload payroll Excel file in sidebar to begin")
else:
    if st.session_state.get("generate", False):
        try:
            mapping_path = Path("Wage-Type-Mapping.xlsx")
            if not mapping_path.exists():
                st.error("❌ Wage-Type-Mapping.xlsx not found")
                st.info("📌 Place Wage-Type-Mapping.xlsx in same folder as dashboard")
            else:
                st.write("📋 **QUALITY CONTROL CHECKS**")
                st.divider()
                
                # Load mapping
                wage_mapping = load_wage_mapping(mapping_path)
                payroll_df = pd.read_excel(uploaded_file, sheet_name=0, header=None)
                
                # CHECK 1: File Structure
                st.write("**CHECK 1:** File Structure Validation")
                errors1, warn1 = check_1_file_structure_validation(payroll_df)
                if errors1:
                    for err in errors1:
                        st.markdown(f'<p class="check-failed">{err}</p>', unsafe_allow_html=True)
                else:
                    st.markdown('<p class="check-passed">✅ File structure valid</p>', unsafe_allow_html=True)
                
                # CHECK 2: Staff ID Validation
                st.write("**CHECK 2:** Staff ID Validation")
                errors2, warn2, valid_ids, blank_ids = check_2_staff_id_validation(payroll_df)
                if errors2:
                    for err in errors2:
                        st.markdown(f'<p class="check-failed">{err}</p>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<p class="check-passed">✅ {valid_ids} valid Staff IDs found</p>', unsafe_allow_html=True)
                if warn2:
                    for w in warn2[:3]:
                        st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                
                # CHECK 3: Wage Code Validation
                st.write("**CHECK 3:** Wage Code Validation")
                errors3, warn3, mapped, unmapped = check_3_wage_code_validation(payroll_df, wage_mapping)
                if errors3:
                    for err in errors3:
                        st.markdown(f'<p class="check-failed">{err}</p>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<p class="check-passed">✅ {mapped} mapped deduction codes found</p>', unsafe_allow_html=True)
                if warn3:
                    for w in warn3[:3]:
                        st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                
                # CHECK 4: Amount Range Validation
                st.write("**CHECK 4:** Amount Range Validation")
                errors4, warn4 = check_4_amount_range_validation(payroll_df, wage_mapping)
                if errors4:
                    for err in errors4[:3]:
                        st.markdown(f'<p class="check-failed">{err}</p>', unsafe_allow_html=True)
                else:
                    st.markdown('<p class="check-passed">✅ All amounts in valid range</p>', unsafe_allow_html=True)
                if warn4:
                    for w in warn4[:3]:
                        st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                
                st.divider()
                st.write("📋 **PROCESSING DATA**")
                
                # Extract deductions
                deductions, error = extract_deductions_row1(payroll_df, wage_mapping)
                
                if error:
                    st.error(f"{error}")
                elif not deductions:
                    st.error("❌ No deductions found")
                else:
                    st.write(f"✅ Extracted {len(deductions)} deduction records")
                    
                    # Apply date rotation
                    deductions = rotate_dates_descending(deductions, month, year)
                    
                    # CHECK 6: Data Quality Report
                    st.write("**CHECK 6:** Data Quality Report")
                    report = check_6_data_quality_report(deductions)
                    if report:
                        col1, col2, col3, col4, col5 = st.columns(5)
                        with col1:
                            st.metric("Total Records", report['total_records'])
                        with col2:
                            st.metric("Unique Employees", report['unique_employees'])
                        with col3:
                            st.metric("Components", report['unique_components'])
                        with col4:
                            st.metric("Avg Amount", f"AED {report['avg_amount']:,.2f}")
                        with col5:
                            st.metric("Total Amount", f"AED {report['total_amount']:,.2f}")
                    
                    # CHECK 7: Missing/Blank Cells
                    st.write("**CHECK 7:** Missing/Blank Cell Detection")
                    warn7 = check_7_missing_blank_cells(payroll_df, wage_mapping)
                    if warn7:
                        for w in warn7[:3]:
                            st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                    else:
                        st.markdown('<p class="check-passed">✅ No blank cells detected</p>', unsafe_allow_html=True)
                    
                    # CHECK 8: Component Consistency
                    st.write("**CHECK 8:** Component Consistency")
                    warn8 = check_8_component_consistency(deductions)
                    if warn8:
                        for w in warn8[:3]:
                            st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                    else:
                        st.markdown('<p class="check-passed">✅ Components consistent</p>', unsafe_allow_html=True)
                    
                    st.divider()
                    st.write("📋 **EXPORT & VALIDATION**")
                    
                    # Generate CSV
                    csv_data = generate_csv_with_two_header_rows(deductions)
                    
                    # CHECK 9: Pre-Download Validation
                    st.write("**CHECK 9:** Pre-Download Validation")
                    errors9, warn9 = check_9_pre_download_validation(csv_data)
                    if errors9:
                        for err in errors9:
                            st.markdown(f'<p class="check-failed">{err}</p>', unsafe_allow_html=True)
                    else:
                        st.markdown('<p class="check-passed">✅ CSV format validated</p>', unsafe_allow_html=True)
                    if warn9:
                        for w in warn9[:2]:
                            st.markdown(f'<p class="check-warning">{w}</p>', unsafe_allow_html=True)
                    
                    if not errors9 and not errors1 and not errors2:
                        st.success("✅ **ALL CHECKS PASSED - READY TO DOWNLOAD!**")
                        
                        st.divider()
                        
                        filename = f"{sheet_name}.csv"
                        
                        col1, col2, col3 = st.columns([1, 1, 1])
                        with col2:
                            st.download_button(
                                f"📥 Download {filename}",
                                csv_data,
                                filename,
                                "text/csv",
                                use_container_width=True,
                                type="primary"
                            )
                        
                        # CHECK 10: Audit Log
                        st.write("**CHECK 10:** Audit Log Entry")
                        audit_entry = check_10_audit_log(sheet_name, len(deductions), report.get('total_amount', 0))
                        st.markdown(f'<p class="check-passed">✅ Logged: {audit_entry["timestamp"]}</p>', unsafe_allow_html=True)
                        
                        with st.expander("📊 View All Data"):
                            st.text(csv_data)
        
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
            import traceback
            traceback.print_exc()
        
        finally:
            st.session_state.generate = False
