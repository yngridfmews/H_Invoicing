import streamlit as st
import pandas as pd
from datetime import datetime
import io

# Configuração da página
st.set_page_config(page_title="Hotello App", layout="wide")

# --- AUTH ---
def login():
    st.sidebar.markdown("### Login")
    password = st.sidebar.text_input("Enter password:", type="password")
    if password == st.secrets["auth_password"]:
        return True
    elif password:
        st.sidebar.error("Incorrect password")
        return False
    return False

# O MENU DEVE VIR DEPOIS DO LOGIN SER APROVADO
if not login():
    st.stop()

# Mostrar menu APÓS o login
menu = st.sidebar.radio("Menu:", ["Invoice", "Credit Notes"])

# ================================
# INVOICE SECTION
# ================================
if menu == "Invoice":
    st.title("📊 Hotello Invoice Generator")
    st.write("Upload the files below for Invoice processing.")

    # Upload files
    chargebee_file = st.file_uploader("ChargeBee Export (.xlsx)", type="xlsx")
    quickbooks_file = st.file_uploader("QuickBooks Export (.xlsx)", type="xlsx")
    bridge_file = st.file_uploader("Bridge (.xlsx)", type="xlsx")
    customers_file = st.file_uploader("Customers_MI (.xlsx)", type="xlsx")

    if chargebee_file and quickbooks_file and bridge_file and customers_file:
        try:
            # Read
            df_chargebee = pd.read_excel(chargebee_file)
            df_qb = pd.read_excel(quickbooks_file, header=4)
            st.write("QuickBooks columns:", df_qb.columns)
            df_bridge = pd.read_excel(bridge_file)
            df_customers_mi = pd.read_excel(customers_file)

            # Initial normalization 
            df_bridge.columns = df_bridge.columns.str.strip()
            df_customers_mi.columns = df_customers_mi.columns.str.strip()

            df_chargebee['Invoice Number'] = df_chargebee['Invoice Number'].astype(str).str.strip().str.lower()
            df_chargebee['Customer Id'] = df_chargebee['Customer Id'].astype(str).str.strip().str.lower()

            df_bridge['Customer ID'] = df_bridge['Customer ID'].astype(str).str.strip().str.lower()
            df_bridge['Subscription No.'] = df_bridge['Subscription No.'].astype(str).str.strip().str.lower()
            df_bridge['Name'] = df_bridge['Name'].astype(str).str.strip().str.lower()
            df_bridge['New Account No. for BC'] = df_bridge['New Account No. for BC'].astype(str).str.strip()

            df_final = pd.DataFrame()
            df_final['Invoice No.'] = df_qb.iloc[:, 0].astype(str).str.strip().str.lower()

            customer_id_lookup = df_chargebee.set_index('Invoice Number')['Customer Id'].to_dict()
            df_final['customer_temp'] = df_final['Invoice No.'].map(customer_id_lookup)

            bridge_long = df_bridge.melt(
                id_vars='New Account No. for BC',
                value_vars=['Customer ID', 'Subscription No.', 'Name'],
                value_name='lookup_value'
            ).drop(columns='variable')

            bridge_long['lookup_value'] = bridge_long['lookup_value'].astype(str).str.strip().str.lower()
            full_bridge_lookup = dict(zip(bridge_long['lookup_value'], bridge_long['New Account No. for BC']))
            df_final['Parent/Customer No.'] = df_final['customer_temp'].map(full_bridge_lookup).fillna('CHECK')
            df_final.drop(columns=['customer_temp'], inplace=True)

            # Additional columns
            df_final.insert(2, 'Subaccount No.', '')
            df_final['Document Date'] = df_qb['Date']
            df_final['Posting Date'] = df_qb['Date']

            days_lookup = dict(zip(
                df_customers_mi['Column1.no_'].astype(str).str.strip(),
                df_customers_mi['Column1.paymenttermscode'].fillna(0).astype(int)
            ))

            df_final['Document Date'] = pd.to_datetime(df_final['Document Date'], errors='coerce')
            days_to_add = df_final['Parent/Customer No.'].astype(str).str.strip().map(days_lookup).fillna(0).astype(int)
            df_final['Due Date'] = df_final['Document Date'] + pd.to_timedelta(days_to_add, unit='D')
            df_final['VAT Date'] = df_qb['Date']

            # Column H
            df_chargebee['Currency'] = df_chargebee['Currency'].astype(str).str.strip()
            currency_lookup = df_chargebee.set_index('Invoice Number')['Currency'].to_dict()
            df_final['Currency Code'] = df_final['Invoice No.'].map(currency_lookup).apply(lambda x: "" if x == "CAD" else x)

            # Column I - Type
            df_final['Type'] = 'Item'

            # Column J - No.
            df_bridge['Account number'] = df_bridge['Account number'].astype(str).str.strip()
            df_bridge['Item'] = df_bridge['Item'].astype(str).str.strip()
            account_to_item = dict(zip(df_bridge['Account number'], df_bridge['Item']))
            df_final['No.'] = df_qb['#']

            def compute_no(row):
                try:
                    account_str = str(int(row['Account #'])) if pd.notna(row['Account #']) else ''
                    lookup_item = account_to_item.get(account_str, account_str)
                    if lookup_item == "49000":
                        no_value = str(row['No.']) if pd.notna(row['No.']) else ""
                        if '-' in no_value:
                            return no_value.split('-')[-1].strip()
                        return ""
                    else:
                        return lookup_item
                except:
                    return "PACKAGE"

            df_final['No.'] = df_final.apply(compute_no, axis=1)

            # Column K - Description
            df_final['Description'] = df_qb['Product/service description']

            # Column L - Quantity
            df_final['Quantity'] = 1

            # Column M - Unit Price Excl. VAT
            df_chargebee['Unit Amount'] = pd.to_numeric(df_chargebee['Unit Amount'], errors='coerce')
            df_chargebee['Discount'] = pd.to_numeric(df_chargebee['Discount'], errors='coerce')
            df_qb['Product/service amount line'] = pd.to_numeric(df_qb['Product/service amount line'], errors='coerce')

            unit_amount_lookup = df_chargebee.drop_duplicates('Invoice Number').set_index('Invoice Number')['Unit Amount'].to_dict()

            def get_unit_price(row):
                if row['Currency Code'] == "":
                    return df_qb.loc[row.name, 'Product/service amount line']
                elif str(row['Description']).strip().lower() == "discount":
                    match = df_chargebee[df_chargebee['Invoice Number'] == row['Invoice No.']]
                    return match['Discount'].sum() if not match.empty else 0
                else:
                    return unit_amount_lookup.get(row['Invoice No.'])

            df_final['Unit Price Excl. VAT'] = df_final.apply(get_unit_price, axis=1)

            # Column N - VAT Prod. Posting Group
            df_final['VAT Prod. Posting Group'] = ''

            # Column P - Deferral Start Date
            df_chargebee['Date From'] = pd.to_datetime(df_chargebee['Date From'], errors='coerce')
            df_chargebee['Date To'] = pd.to_datetime(df_chargebee['Date To'], errors='coerce')
            df_dates = df_chargebee.groupby('Invoice Number')[['Date From', 'Date To']].first().reset_index()

            df_final = df_final.merge(
                df_dates,
                left_on='Invoice No.',
                right_on='Invoice Number',
                how='left'
            )

            df_final['Deferral Start Date'] = df_final['Date From'].fillna('CHECK')
            df_final['Deferral End Date'] = df_final['Date To'].fillna('CHECK')
            df_final['Deferral Code'] = df_final['Deferral Start Date'].apply(
                lambda x: 'AR' if pd.notna(x) and str(x).strip() != '' else ''
            )

            # Small values
            df_final['Unit Price Excl. VAT'] = pd.to_numeric(df_final['Unit Price Excl. VAT'], errors='coerce')
            mask_small_amount_and_deferral = (
                (df_final['Unit Price Excl. VAT'].abs() < 0.05) &
                (df_final['Deferral Start Date'] != df_final['Deferral End Date'])
            )
            df_final.loc[mask_small_amount_and_deferral, ['Deferral Code', 'Deferral Start Date', 'Deferral End Date']] = ""

            df_final['CUSTOMER Dimension'] = df_final['Parent/Customer No.']

            new_columns = [
                "BU Dimension", "C Dimension", "ENTITY Dimension", "IC Dimension",
                "PRICE Dimension", "PRODUCT Dimension", "RECURRENCE Dimension",
                "SUBPRODUCT Dimension", "TAX DEDUCTIBILITY Dimension",
                "Reseller Code", "Apply Overpayments"]

            # Adiciona cada coluna com valor vazio ""
            for col in new_columns:
                df_final[col] = ""

            # Correct deferral dates if more than 1 invoice

            # Remove linhas onde 'Invoice No.' está em branco ou NaN
            df_final = df_final[df_final['Invoice No.'].notna() & (df_final['Invoice No.'].astype(str).str.strip() != '')]

            # Lista com a ordem desejada das colunas
            ordered_columns = [
                "Invoice No.","Parent/Customer No.","Subaccount No.","Document Date","Posting Date","Due Date","VAT Date","Currency Code","Type",
                "No.","Description","Quantity","Unit Price Excl. VAT","VAT Prod. Posting Group","Deferral Code","Deferral Start Date","Deferral End Date",
                "BU Dimension","C Dimension","ENTITY Dimension","IC Dimension","PRICE Dimension","PRODUCT Dimension","RECURRENCE Dimension",
                "SUBPRODUCT Dimension","TAX DEDUCTIBILITY Dimension","CUSTOMER Dimension","Reseller Code","Apply Overpayments"]

            # Reordenar as colunas do DataFrame
            df_final = df_final[ordered_columns]

            # Exportar para Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_final.to_excel(writer, index=False, sheet_name="CreditNotes")
                workbook = writer.book
                worksheet = writer.sheets["CreditNotes"]

                date_columns = [
                    "Document Date", "Posting Date", "Due Date",
                    "VAT Date", "Deferral Start Date", "Deferral End Date"
                ]
                for col_name in date_columns:
                    if col_name in df_final.columns:
                        col_idx = df_final.columns.get_loc(col_name) + 1
                        for row in range(2, len(df_final) + 2):
                            cell = worksheet.cell(row=row, column=col_idx)
                            if isinstance(cell.value, (datetime, pd.Timestamp)):
                                cell.number_format = 'dd/mm/yyyy'

            output.seek(0)

            st.success("✅ Invoices file generated.")

            # 👇 Aqui vem o preview da tabela
            st.subheader("🔍 Preview of Generated Invoice File")
            st.dataframe(df_final.head(50))

            st.download_button(
                label="📥 Download Invoices",
                data=output,
                file_name=f"Hotello_Invoicing_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except Exception as e:
            st.error(f"Error during the process: {e}")
        else:
            st.info("⏳ Please, upload all the needed files above")


# ================================
# CREDIT NOTES SECTION
# ================================
elif menu == "Credit Notes":
    st.title("📉 Hotello Credit Notes Generator")
    st.write("Upload the files below for Credit Notes processing.")

    chargebee_file = st.file_uploader("ChargeBee Export (.xlsx)", type="xlsx", key="cb_credit")
    quickbooks_file = st.file_uploader("QuickBooks Export (.xlsx)", type="xlsx", key="qb_credit")
    bridge_file = st.file_uploader("Bridge (.xlsx)", type="xlsx", key="bridge_credit")

    if chargebee_file and quickbooks_file and bridge_file:

        import unicodedata
        from openpyxl.styles import numbers

        try:
            df_cb_cm = pd.read_excel(chargebee_file)
            df_qb_cm = pd.read_excel(quickbooks_file, header=3)
            df_bridgecm = pd.read_excel(bridge_file)

            if '#' in df_qb_cm.columns:
                df_qb_cm = df_qb_cm.rename(columns={'#': 'No.'})
            if 'Distribution account number' in df_qb_cm.columns:
                df_qb_cm = df_qb_cm.rename(columns={'Distribution account number': 'Account No.'})
            if 'Amount' in df_qb_cm.columns:
                df_qb_cm = df_qb_cm.rename(columns={'Amount': 'Amount line'})
            

            def normalize_str(s):
                if pd.isna(s):
                    return ''
                s = str(s).strip().lower()
                s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
                s = s.split('.')[0]
                return s

            for col in ['Credit Note Number', 'Customer Id', 'Currency', 'Description']:
                if col in df_cb_cm.columns:
                    df_cb_cm[col] = df_cb_cm[col].astype(str).apply(normalize_str)

            for col in ['No.', 'Account No.']:
                if col in df_qb_cm.columns:
                    df_qb_cm[col] = df_qb_cm[col].astype(str).apply(normalize_str)

            df_cb_cm['Unit Amount'] = pd.to_numeric(df_cb_cm['Unit Amount'], errors='coerce')
            df_qb_cm['Amount line'] = pd.to_numeric(df_qb_cm['Amount line'], errors='coerce')
            df_bridgecm['Account number'] = df_bridgecm['Account number'].astype(str).apply(normalize_str)
            df_bridgecm['Item'] = df_bridgecm['Item'].astype(str).str.strip()

            df_cb_cm['merge_key'] = df_cb_cm['Credit Note Number'] + '||' + df_cb_cm['Description']
            df_credit_notes = df_cb_cm.copy()
            df_credit_notes['Credit Memo No.'] = df_credit_notes['Credit Note Number']
            df_credit_notes['merge_key'] = df_credit_notes['Credit Memo No.'] + '||' + df_credit_notes['Description']

            df_cb_cm['Customer Id'] = df_cb_cm['Customer Id'].astype(str).str.strip().str.lower()
            df_bridgecm['Customer ID'] = df_bridgecm['Customer ID'].astype(str).str.strip().str.lower()
            df_bridgecm['New Account No. for BC '] = df_bridgecm['New Account No. for BC '].astype(str).str.strip()

            customer_lookup = df_cb_cm.set_index('Credit Note Number')['Customer Id'].to_dict()
            bridge_lookup = df_bridgecm.set_index('Customer ID')['New Account No. for BC '].to_dict()
            df_credit_notes['customer_temp'] = df_credit_notes['Credit Memo No.'].map(customer_lookup)
            df_credit_notes['Parent/Customer No.'] = df_credit_notes['customer_temp'].map(bridge_lookup).fillna("CHECK")
            df_credit_notes.drop(columns=['customer_temp'], inplace=True)

            df_credit_notes["Subaccount No."] = ""

            df_cb_cm['Date From'] = pd.to_datetime(df_cb_cm['Date From'], errors='coerce')
            df_cb_cm['Date To'] = pd.to_datetime(df_cb_cm['Date To'], errors='coerce')
            df_credit_notes['Document Date'] = df_cb_cm['Date From']
            df_credit_notes['Posting Date'] = df_cb_cm['Date From']
            df_credit_notes['Due Date'] = df_cb_cm['Date From']
            df_credit_notes['VAT Date'] = df_cb_cm['Date From']

            currency_lookup = df_cb_cm.set_index('Credit Note Number')['Currency'].to_dict()
            df_credit_notes['Currency Code'] = df_credit_notes['Credit Memo No.'].map(currency_lookup).apply(lambda x: "" if x == "cad" else x)

            df_credit_notes['Credit Note Reason Code'] = 'HOT CORRECTION'
            df_credit_notes['Responsibility Center'] = 'HOT'
            df_credit_notes['Block Overpayment'] = 'TRUE'
            df_credit_notes['Type'] = 'Item'

            df_credit_notes['Quantity'] = 1
            df_credit_notes['Unit Price Excl. VAT'] = df_credit_notes['Unit Amount']
            df_credit_notes['VAT Prod. Posting Group'] = ""

            df_credit_notes['merge_key_cb'] = df_credit_notes['merge_key']
            date_from_map = dict(zip(df_cb_cm['merge_key'], df_cb_cm['Date From']))
            date_to_map = dict(zip(df_cb_cm['merge_key'], df_cb_cm['Date To']))
            df_credit_notes['Deferral Start Date'] = df_credit_notes['merge_key_cb'].map(date_from_map).fillna('CHECK')
            df_credit_notes['Deferral End Date'] = df_credit_notes['merge_key_cb'].map(date_to_map).fillna('CHECK')
            df_credit_notes['Deferral Code'] = df_credit_notes['Deferral Start Date'].apply(lambda x: 'AR' if x != 'CHECK' else '')

            # REMOVE DEFERRAL PARA VALORES ZERADOS OU NULOS
            mask_small = (
                df_credit_notes['Unit Price Excl. VAT'].isna() |
                (df_credit_notes['Unit Price Excl. VAT'].abs() < 0.05)
            ) & (
                df_credit_notes['Deferral Start Date'] != df_credit_notes['Deferral End Date']
            )
            df_credit_notes.loc[mask_small, ['Deferral Code', 'Deferral Start Date', 'Deferral End Date']] = ""

            # AJUSTAR COLUNA No. COM BASE NA QUICKBOOKS + BRIDGE
            df_credit_notes['Unit Price Rounded'] = df_credit_notes['Unit Price Excl. VAT'].round(2)
            df_qb_cm['Amount match'] = df_qb_cm['Amount line'].abs().round(2)

            amount_to_account = df_qb_cm.drop_duplicates(subset='Amount match').set_index('Amount match')['Account No.'].to_dict()
            df_credit_notes['Account Matched'] = df_credit_notes['Unit Price Rounded'].map(amount_to_account)

            bridge_dict = df_bridgecm.drop_duplicates(subset='Account number').set_index('Account number')['Item'].to_dict()
            df_credit_notes['No.'] = df_credit_notes['Account Matched'].map(bridge_dict).fillna('PACKAGE')

            df_credit_notes['CUSTOMER Dimension'] = df_credit_notes['Parent/Customer No.']

            dim_cols = [
                "BU Dimension", "C Dimension", "ENTITY Dimension", "IC Dimension",
                "PRICE Dimension", "PRODUCT Dimension", "RECURRENCE Dimension",
                "SUBPRODUCT Dimension", "TAX DEDUCTIBILITY Dimension", "Reseller Code"
            ]
            for col in dim_cols:
                df_credit_notes[col] = ""

            final_cols = [
                "Credit Memo No.", "Parent/Customer No.", "Subaccount No.", "Document Date", "Posting Date", "Due Date", "VAT Date", "Currency Code",
                "Credit Note Reason Code", "Responsibility Center", "Block Overpayment", "Type", "No.", "Description", "Quantity",
                "Unit Price Excl. VAT", "VAT Prod. Posting Group", "Deferral Code", "Deferral Start Date", "Deferral End Date",
                "BU Dimension", "C Dimension", "ENTITY Dimension", "IC Dimension", "PRICE Dimension", "PRODUCT Dimension",
                "RECURRENCE Dimension", "SUBPRODUCT Dimension", "TAX DEDUCTIBILITY Dimension", "CUSTOMER Dimension", "Reseller Code"
            ]
            for col in final_cols:
                if col not in df_credit_notes.columns:
                    df_credit_notes[col] = ""

            df_credit_notes['Description'] = df_credit_notes['Description'].fillna('').astype(str).replace('nan', '')
            df_credit_notes = df_credit_notes[df_credit_notes['Credit Memo No.'].notna()]
            df_credit_notes = df_credit_notes[df_credit_notes['Credit Memo No.'].astype(str).str.strip() != '']
            df_credit_notes = df_credit_notes[final_cols]

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_credit_notes.to_excel(writer, index=False, sheet_name="CreditNotes")
                workbook = writer.book
                worksheet = writer.sheets["CreditNotes"]

                date_columns = [
                    "Document Date", "Posting Date", "Due Date",
                    "VAT Date", "Deferral Start Date", "Deferral End Date"
                ]
                for col_name in date_columns:
                    if col_name in df_credit_notes.columns:
                        col_idx = df_credit_notes.columns.get_loc(col_name) + 1
                        for row in range(2, len(df_credit_notes) + 2):
                            cell = worksheet.cell(row=row, column=col_idx)
                            if isinstance(cell.value, (datetime, pd.Timestamp)):
                                cell.number_format = 'dd/mm/yyyy'

            output.seek(0)

            st.success("✅ Credit Notes file generated.")
            # 👇 Aqui vem o preview da tabela
            st.subheader("🔍 Preview of Generated File")
            st.dataframe(df_credit_notes.head(50))

            st.download_button(
                label="📥 Download Credit Notes",
                data=output,
                file_name=f"Hotello_Credit_Notes_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except Exception as e:
            st.error(f"❌ Error during processing: {e}")
    else:
        st.info("⏳ Please, upload all required files above.")