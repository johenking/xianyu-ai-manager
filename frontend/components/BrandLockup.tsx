import React from 'react';

interface BrandLockupProps {
  className?: string;
  subtitle?: string;
}

const BrandLockup: React.FC<BrandLockupProps> = ({ className = '', subtitle }) => (
  <div className={`flex items-center gap-3 ${className}`}>
    <div className="flex h-10 w-10 shrink-0 -rotate-3 items-center justify-center rounded-xl bg-[#FFE815] shadow-lg shadow-yellow-200">
      <span className="text-xl font-extrabold text-black">闲</span>
    </div>
    <div className="min-w-0">
      <div className="flex items-center gap-2 whitespace-nowrap text-xl font-extrabold text-gray-900">
        <span>闲鱼智控</span>
        <span className="rounded bg-black px-1.5 py-0.5 text-xs text-[#FFE815]">PRO</span>
      </div>
      {subtitle ? <p className="mt-0.5 text-xs font-medium text-gray-500">{subtitle}</p> : null}
    </div>
  </div>
);

export default BrandLockup;
