import React, { useState } from 'react';
import { ValidateResponse, FieldIssue } from '../types';

export const CheckInvoice: React.FC = () => {
  const [mode, setMode] = useState<'form' | 'json'>('form');

  // Quick form fields (most common fields)
  const [supplierName, setSupplierName] = useState('ACME Innovations Sdn Bhd');
  const [supplierTin, setSupplierTin] = useState('C1234567890');
  const [invoiceNumber, setInvoiceNumber] = useState('INV-2026-0042');
  const [invoiceDate, setInvoiceDate] = useState('2026-09-04T09:30:00Z');
  const [totalAmount, setTotalAmount] = useState('1500.00');

  // JSON input
  const [jsonInput, setJsonInput] = useState(`{
  "Supplier's Name": "ACME Innovations Sdn Bhd",
  "Supplier's TIN": "C1234567890",
  "e-Invoice Code / Number": "INV-2026-0042",
  "e-Invoice Date and Time": "2026-09-04T09:30:00Z",
  "Total Payable Amount": "1500.00"
}`);

  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ValidateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showPresent, setShowPresent] = useState(false);

  const getPayload = (): Record<string, any> | null => {
    if (mode === 'form') {
      const payload: Record<string, any> = {};
      if (supplierName.trim()) payload["Supplier's Name"] = supplierName.trim();
      if (supplierTin.trim()) payload["Supplier's TIN"] = supplierTin.trim();
      if (invoiceNumber.trim()) payload["e-Invoice Code / Number"] = invoiceNumber.trim();
      if (invoiceDate.trim()) payload["e-Invoice Date and Time"] = invoiceDate.trim();
      if (totalAmount.trim()) payload["Total Payable Amount"] = totalAmount.trim();
      return payload;
    } else {
      try {
        const parsed = JSON.parse(jsonInput);
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          setError('JSON must be a key-value object representing the invoice fields.');
          return null;
        }
        return parsed;
      } catch (err) {
        setError('Invalid JSON format. Please verify your syntax.');
        return null;
      }
    }
  };

  const handleValidate = async () => {
    setError(null);
    const invoicePayload = getPayload();
    if (!invoicePayload) return;

    setLoading(true);
    try {
      const response = await fetch('/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice: invoicePayload }),
      });

      const data = await response.json();
      if (!response.ok) {
        setError(data.error || 'Failed to validate invoice.');
        setReport(null);
      } else {
        setReport(data as ValidateResponse);
      }
    } catch (err) {
      setError('Could not connect to the validation server. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const loadSample = (type: 'minimal' | 'compliant') => {
    setError(null);
    if (type === 'minimal') {
      const minData = {
        "Supplier's Name": "Malayan Retailers Sdn Bhd",
        "Total Payable Amount": "250.00",
      };
      setJsonInput(JSON.stringify(minData, null, 2));
      setSupplierName('Malayan Retailers Sdn Bhd');
      setSupplierTin('');
      setInvoiceNumber('');
      setInvoiceDate('');
      setTotalAmount('250.00');
    } else {
      const fullData = {
        "Supplier's Name": "Sdn Bhd Enterprise",
        "Supplier's TIN": "C2345678901",
        "Supplier's Registration / Identification Number / Passport Number": "202401001234",
        "Buyer's Name": "Consumer One",
        "Buyer's TIN": "EI00000000010",
        "Buyer's Registration / Identification Number / Passport Number": "NA",
        "e-Invoice Code / Number": "INV-2026-9999",
        "e-Invoice Date and Time": "2026-09-04T12:00:00Z",
        "Total Payable Amount": "105.00",
        "Total Excluding Tax": "100.00",
        "Total Including Tax": "105.00",
        "Total Tax Amount": "5.00",
        "Invoice Currency Code": "MYR",
      };
      setJsonInput(JSON.stringify(fullData, null, 2));
      setSupplierName(fullData["Supplier's Name"]);
      setSupplierTin(fullData["Supplier's TIN"]);
      setInvoiceNumber(fullData["e-Invoice Code / Number"]);
      setInvoiceDate(fullData["e-Invoice Date and Time"]);
      setTotalAmount(fullData["Total Payable Amount"]);
    }
  };

  return (
    <div className="view-container check-invoice-container">
      <div className="validator-banner">
        <strong>Deterministic Validation Engine:</strong> Validates fields directly against official
        IRBM Appendix 1 specifications. This endpoint uses <em>no LLM tokens</em> and remains 100% available
        even if daily question quota is spent.
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div className="input-mode-tabs">
          <button
            type="button"
            className={`mode-tab-btn ${mode === 'form' ? 'active' : ''}`}
            onClick={() => setMode('form')}
          >
            Quick Form
          </button>
          <button
            type="button"
            className={`mode-tab-btn ${mode === 'json' ? 'active' : ''}`}
            onClick={() => setMode('json')}
          >
            Raw JSON
          </button>
        </div>

        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => loadSample('minimal')}
          >
            Incomplete Sample
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => loadSample('compliant')}
          >
            Compliant Sample
          </button>
        </div>
      </div>

      {mode === 'form' ? (
        <div className="form-grid">
          <div className="form-field full-width">
            <label htmlFor="sup-name">Supplier's Name *</label>
            <input
              id="sup-name"
              type="text"
              value={supplierName}
              onChange={(e) => setSupplierName(e.target.value)}
              placeholder="e.g. Syarikat Maju Jaya Sdn Bhd"
            />
          </div>

          <div className="form-field">
            <label htmlFor="sup-tin">Supplier's TIN *</label>
            <input
              id="sup-tin"
              type="text"
              value={supplierTin}
              onChange={(e) => setSupplierTin(e.target.value)}
              placeholder="e.g. C1234567890"
            />
          </div>

          <div className="form-field">
            <label htmlFor="inv-num">Invoice Number (Code) *</label>
            <input
              id="inv-num"
              type="text"
              value={invoiceNumber}
              onChange={(e) => setInvoiceNumber(e.target.value)}
              placeholder="e.g. INV-2026-0001"
            />
          </div>

          <div className="form-field">
            <label htmlFor="inv-date">Invoice Date and Time *</label>
            <input
              id="inv-date"
              type="text"
              value={invoiceDate}
              onChange={(e) => setInvoiceDate(e.target.value)}
              placeholder="e.g. 2026-09-04T10:00:00Z"
            />
          </div>

          <div className="form-field">
            <label htmlFor="inv-total">Total Payable Amount *</label>
            <input
              id="inv-total"
              type="text"
              value={totalAmount}
              onChange={(e) => setTotalAmount(e.target.value)}
              placeholder="e.g. 1500.00"
            />
          </div>
        </div>
      ) : (
        <div className="json-editor-box">
          <label htmlFor="json-textarea" style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--muted)' }}>
            Invoice JSON Object:
          </label>
          <textarea
            id="json-textarea"
            className="json-textarea"
            value={jsonInput}
            onChange={(e) => setJsonInput(e.target.value)}
            placeholder='{ "Supplier&#39;s Name": "...", "Supplier&#39;s TIN": "..." }'
            rows={8}
          />
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="button"
          className="btn-primary"
          onClick={handleValidate}
          disabled={loading}
          style={{ minWidth: '130px', justifyContent: 'center' }}
        >
          {loading ? 'Checking...' : 'Check Invoice'}
        </button>
      </div>

      {error && (
        <div className="error-callout" role="alert">
          {error}
        </div>
      )}

      {report && (
        <div className="report-card">
          <div className={`report-status-banner ${report.valid ? 'valid' : 'invalid'}`}>
            <span>
              {report.valid
                ? 'All mandatory fields present'
                : `Missing ${report.missing_mandatory.length} mandatory field${report.missing_mandatory.length > 1 ? 's' : ''}`}
            </span>
            <span style={{ fontSize: '0.8rem', opacity: 0.9 }}>
              Checked {report.checked} fields
            </span>
          </div>

          {report.missing_mandatory.length > 0 && (
            <div className="report-section">
              <h4 style={{ color: '#b91c1c' }}>
                Missing Mandatory Fields ({report.missing_mandatory.length})
              </h4>
              <ul className="report-items-list">
                {report.missing_mandatory.map((issue: FieldIssue) => (
                  <li key={issue.no} className="report-item" style={{ borderColor: '#fecaca', background: '#fff5f5' }}>
                    <div className="report-item-title">
                      <span>#{issue.no} {issue.name}</span>
                      <span className="report-item-ref">{issue.section}</span>
                    </div>
                    {issue.category && (
                      <div style={{ fontSize: '0.74rem', color: 'var(--muted)' }}>
                        Category: {issue.category}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.check_conditional.length > 0 && (
            <div className="report-section">
              <h4 style={{ color: '#b45309' }}>
                Conditional Fields to Verify ({report.check_conditional.length})
              </h4>
              <ul className="report-items-list">
                {report.check_conditional.map((issue: FieldIssue) => (
                  <li key={issue.no} className="report-item">
                    <div className="report-item-title">
                      <span>#{issue.no} {issue.name}</span>
                      <span className="report-item-ref">{issue.section}</span>
                    </div>
                    {issue.condition && (
                      <div className="report-item-condition">
                        <strong>Condition:</strong> {issue.condition}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.unknown_keys && report.unknown_keys.length > 0 && (
            <div className="report-section">
              <h4 style={{ color: '#854d0e' }}>
                Unrecognised Fields ({report.unknown_keys.length})
              </h4>
              <div className="unknown-keys-chip-container">
                {report.unknown_keys.map((k, idx) => (
                  <span key={idx} className="unknown-key-chip">
                    {k}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="report-section">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setShowPresent(!showPresent)}
              style={{ alignSelf: 'flex-start' }}
            >
              {showPresent ? 'Hide' : 'Show'} Recognized Present Fields ({report.present.length})
            </button>

            {showPresent && (
              <div className="present-keys-summary">
                {report.present.join(', ') || 'None'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
