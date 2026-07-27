import React, { useEffect, useState } from 'react';
import { User as UserIcon } from 'lucide-react';

interface BuyerAvatarProps {
  src?: string;
  className: string;
}

const BuyerAvatar: React.FC<BuyerAvatarProps> = ({ src, className }) => {
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [src]);

  if (!src || failed) {
    return (
      <div
        aria-label="买家头像占位"
        className={`${className} bg-gray-100 flex items-center justify-center text-gray-300`}
      >
        <UserIcon className="w-5 h-5" />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt=""
      aria-label="买家头像"
      referrerPolicy="no-referrer"
      className={className}
      onError={() => setFailed(true)}
    />
  );
};

export default BuyerAvatar;
