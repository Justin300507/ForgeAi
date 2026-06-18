import React from 'react';
import PriorityBadge from './PriorityBadge';

export default function TaskCard({ task }) {
  return (
    <div>
      <h3>{task.title}</h3>
      <PriorityBadge priority={task.priority} />
    </div>
  );
}