package lab_dream.exception;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.Map;

/**
 * 全局错误处理。
 *
 * 服务器也会出错——出错的正确姿势是：说清楚发生了什么（状态码），
 * 并用一句人话告诉对方（message）。404 不必冰冷。
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(StoryException.class)
    public ResponseEntity<Map<String, Object>> story(StoryException e) {
        return respond(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler(GuardException.class)
    public ResponseEntity<Map<String, Object>> guard(GuardException e) {
        return respond(HttpStatus.FORBIDDEN, e.getMessage());
    }

    /** 请求了一条不存在的线路——404 也用这个世界的方式说话 */
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<Map<String, Object>> notFound(NoResourceFoundException e) {
        return respond(HttpStatus.NOT_FOUND,
                "这条线路不存在。回到实验室，按当前关卡给出的路径发请求。");
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, Object>> badRequest(IllegalArgumentException e) {
        return respond(HttpStatus.BAD_REQUEST, e.getMessage());
    }

    private ResponseEntity<Map<String, Object>> respond(HttpStatus status, String message) {
        return ResponseEntity.status(status).body(Map.of(
                "error", status.value(),
                "message", message
        ));
    }
}
