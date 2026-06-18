import React from 'react';

export default function PriorityBadge({ priority }) {
  const colors = ['green', 'orange', 'red'];
  const labels = ['Low', 'Medium', 'High'];
  
  return (
    <span style={{ color: colors[priority - 1] }}>
      {labels[priority - 1]}
    </span>
  );
}