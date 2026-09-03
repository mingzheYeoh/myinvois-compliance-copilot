import React from 'react';
import { HealthResponse } from '../types';

interface HeaderProps {
  health: HealthResponse | null;
  isWakingUp: boolean;
  activeTab: 'ask' | 'check';
  setActiveTab: (tab: 'ask' | 'check') => void;
}

export const Header: React.FC<HeaderProps> = ({
  health,
  isWakingUp,
  activeTab,
  setActiveTab,
}) => {
  const versions = health?.guideline_versions || {};

  return (
    <header className="header-container">
      <div className="title-area">
        <h1>MyInvois Compliance Copilot</h1>
        <p className="subtitle">
          Malaysian LHDN e-Invoice compliance assistant
        </p>
      </div>

      {isWakingUp && (
        <div className="cold-start-banner" role="status" aria-live="polite">
          Waking up (~35s) &mdash; Service is starting up. Ask assistant will be available once ready.
        </div>
      )}

      <div className="versions-bar">
        <span className="versions-label">Grounded in:</span>
        {Object.keys(versions).length > 0 ? (
          Object.entries(versions).map(([docKey, ver], idx, arr) => {
            let label = docKey;
            if (docKey === 'general_guideline') label = 'General Guideline';
            else if (docKey === 'specific_guideline') label = 'Specific Guideline';
            else if (docKey === 'general_faq') label = 'FAQ';
            return (
              <span key={docKey} className="version-chip">
                {label} v{ver}{idx < arr.length - 1 ? ' ·' : ''}
              </span>
            );
          })
        ) : (
          <span className="version-chip" style={{ opacity: 0.6 }}>
            {isWakingUp ? 'Loading document versions…' : 'Guideline versions unavailable'}
          </span>
        )}
      </div>

      <nav className="tabs-nav" aria-label="Main Navigation">
        <button
          className={`tab-btn ${activeTab === 'ask' ? 'active' : ''}`}
          onClick={() => setActiveTab('ask')}
          type="button"
        >
          Ask Assistant
        </button>
        <button
          className={`tab-btn ${activeTab === 'check' ? 'active' : ''}`}
          onClick={() => setActiveTab('check')}
          type="button"
        >
          Check Invoice
          <span className="tab-subtext">· No AI quota needed</span>
        </button>
      </nav>
    </header>
  );
};
