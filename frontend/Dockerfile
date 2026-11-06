FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY index.html .
COPY public ./public
COPY src ./src
COPY tsconfig.json .
COPY vite.config.ts .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
