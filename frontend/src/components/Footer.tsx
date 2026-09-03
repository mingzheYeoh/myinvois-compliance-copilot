import React from 'react';

interface FooterProps {
  budget?: {
    limit: number;
    used: number | null;
    remaining: number | null;
  } | null;
}

export const Footer: React.FC<FooterProps> = ({ budget }) => {
  const remaining = budget?.remaining;
  const limit = budget?.limit;

  return (
    <footer className="footer-container">
      {remaining !== null && remaining !== undefined && limit ? (
        <div className="footer-budget">
          Daily AI token budget: {remaining.toLocaleString()} of {limit.toLocaleString()} remaining
        </div>
      ) : null}
      <p>
        Informational only &mdash; not tax or legal advice. Verify every answer against
        the cited sections of the official LHDN documents before relying on it.
      </p>
    </footer>
  );
};
